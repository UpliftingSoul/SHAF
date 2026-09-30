import re

from app.config import settings
from app.core import embeddings, rerank
from app.kb import milvus_client

_CLAUSE = re.compile(r"[,，;；?？。]")
_MIN_CLAUSE = 4          # 太短的碎片(「怎么办」)当不了子查询


def split_clauses(query: str) -> list[str]:
    """
    按标点切割多意图问句为子句；无法切出≥2条有效子句则原样返回原句。
    适用场景：单句包含多个独立问题，例如「我在新疆下单80块，运费怎么算，会员的免运费能不能抵」。
    问题：完整句子向量容易被某一个意图主导，其余意图召回结果排不到前列。
    方案：各子句独立检索后轮转合并，保障多意图均有候选结果。
    约束：必须搭配rerank精排；无精排会引入噪声降低效果。
    评估参考：开启重排指标+0.02，无重排场景指标‑0.04~‑0.06。
    ⚠ 注意（当前局限）：仅为标点规则切分，非语义意图拆分；单意图附带逗号修饰时会产生误切。
    """
    # 正则切分，过滤过短碎片文本
    parts = [p.strip() for p in _CLAUSE.split(query) if len(p.strip()) >= _MIN_CLAUSE]
    # 有效片段>=2才使用切分结果，否则返回原始问句
    return parts if len(parts) >= 2 else [query]


def _merge_round_robin(lists: list[list[dict]]) -> list[dict]:
    """
    多路子句召回结果轮转合并。
    策略：依次从每一路取第i位结果；按文档id全局去重；
    同一section_path只保留最先出现条目，同章节重复文档后置到tail区。
    """
    out, seen_id, seen_sec, tail = [], set(), set(), []  # 主序 / 已见id / 已见章节 / 章节重复后置区

    # --- 第1块：按"轮次"遍历，每轮从各路各取一个候选 ---
    # 轮数 = 最长那一路的长度；短的路在轮次超过自身长度时直接跳过
    max_len = max((len(x) for x in lists), default=0)
    for i in range(max_len):
        # 依次遍历各路
        for lst in lists:
            if i >= len(lst):
                continue  # 该路没有第 i 位，跳过
            h = lst[i]  # 本轮该路取出的候选文档

            # --- 第2块：文档级去重（同一 chunk 全集合只保留一次）---
            key = h.get("id") or (h.get("section_path", ""), h.get("question", ""))
            if key in seen_id:
                continue  # 已出现过，丢弃
            seen_id.add(key)

            # --- 第3块：章节级去重（同一 section 靠前只放一条，其余后置）---
            sec = h.get("section_path") or ""
            if sec in seen_sec:
                tail.append(h)  # 该章节已有候选，压入尾部候补区
            else:
                seen_sec.add(sec)
                out.append(h)  # 该章节首次出现，进主序列

    # --- 第4块：拼装返回 = 主序列 + 同章节重复的后置候补 ---
    return out + tail


def arrange_head_tail(items: list) -> list:
    """
    调整候选文档顺序缓解 LLM lost‑in‑the‑middle 问题。
    排布规则：最高分放首位，次高分移至末尾，剩余文档顺序保持不变。
    当条目数≤2时无需重排，直接返回原序列。
    """
    if len(items) <= 2:
        return items
    # [第0名] + [第2名及以后] + [第1名]
    return [items[0], *items[2:], items[1]]


async def search_knowledge(
    query: str, strategy: str = "hybrid_rerank", top_k: int | None = None,
    category: str | None = None, client=None, collection: str = "knowledge",
    bm25_text: str | None = None, split: bool = True,
) -> list[dict]:
    """strategy: vector | bm25 | hybrid | hybrid_rerank。
    返回最终排序的 hit(hybrid_rerank 含 rerank_score);不做首尾组装(调用侧按需)。

    query：dense 向量化与重排用的标准问法；bm25_text：BM25/sparse 检索文本(默认同 query);
    ❗调用侧传入「标准问法 + 同义词扩展」只作用于 BM25 一侧,不污染 dense query(spec §2)。

    所有 Milvus 同步调用(建集合/检索)经 milvus_client.acall 固定到专用单线程执行,
    客户端也在该线程惰性创建 —— 规避 gRPC 客户端跨线程共享在 async server 下的间歇空返回。
    """
    top_k = top_k or settings.rerank_top_k
    bt = bm25_text or query  # BM25 检索文本;dense 始终用干净的 query

    # 一问多意图:按子句各检索一遍再轮转合并,免得一个意图把名额占满(见 split_clauses)
    if split and settings.subquery_split:
        clauses = split_clauses(query)
        if len(clauses) >= 2:
            per = [await search_knowledge(c, strategy=strategy, top_k=top_k,
                                          category=category, client=client,
                                          collection=collection, split=False)
                   for c in clauses]
            return _merge_round_robin(per)[:top_k]

    if strategy == "bm25":
        def work():
            c = client or milvus_client.get_client()
            milvus_client.ensure_collection(c, collection=collection)
            return milvus_client.bm25_search(c, bt, top_k, category, collection)
        return await milvus_client.acall(work)

    # 第三章内容：经典Top-k稠密检索
    if strategy == "vector":
        vec = await embeddings.embed_query(query)

        def work():
            c = client or milvus_client.get_client()
            milvus_client.ensure_collection(c, collection=collection)
            return milvus_client.dense_search(c, vec, top_k, category, collection)
        return await milvus_client.acall(work)

    # hybrid / hybrid_rerank:dense 用 query 向量、BM25 用 bt,先混合召回 recall_top_k
    vec = await embeddings.embed_query(query)

    def work():
        c = client or milvus_client.get_client()
        milvus_client.ensure_collection(c, collection=collection)
        return milvus_client.hybrid_search(
            c, vec, bt, settings.recall_top_k, settings.recall_top_k, category, collection)
    hits = await milvus_client.acall(work)

    if strategy == "hybrid":
        return hits[:top_k]

    # hybrid_rerank:对召回结果精排(rerank 走上游 async,不进 Milvus 线程)
    docs = [f"{h['question']} {h['answer']}" for h in hits]
    ranked = await rerank.rerank(query, docs, top_n=top_k)
    out = []
    for idx, score in ranked:
        h = dict(hits[idx]); h["rerank_score"] = score
        out.append(h)
    return out
