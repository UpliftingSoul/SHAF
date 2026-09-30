from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.config import settings
from app.core import query_understanding, retrieval, selfcheck
from app.tools import registry


class FaqInput(BaseModel):
    keyword: str = Field(description="用户咨询的政策/规则/操作类问题(可用原话)")
    category: str | None = Field(default=None, description="可选:按品类过滤,如『运费』『退货』『商品手册』")


@tool(args_schema=FaqInput)
async def query_faq(keyword: str, category: str | None = None) -> dict:
    """查询常见问题/政策知识库(混合检索+重排)。用于政策、规则、时效、费用、商品手册等通用问题。
    返回带编号证据供作答引用;证据不足时返回 sufficient=False,请据此向用户拒答。"""
    u = await query_understanding.understand(keyword)
    query = u["standard"]
    # 同义词扩展只拼进 BM25 检索文本(检索侧),不改 dense query、不改标准问法语义(ch04 spec §2)
    # * BM25 是词面匹配，多给几个近义词纯赚，用户说「邮费」而文档写「运费」，扩展词正好把这个缺口补上。
    # * 但向量检索不一样，它编码的是整句的语义，在标准问法后面缀一串同义词，句子的语义重心会被这串词带偏，检索结果反而变差。
    bm25_text = query + (" " + " ".join(u["expanded"]) if u["expanded"] else "")

    # 【检索召回】
    hits = await retrieval.search_knowledge(
        query, strategy="hybrid_rerank", category=category, bm25_text=bm25_text)

    # 品类过滤容错：category由模型生成，容易和知识库实际分类值对不上
    # 品类过滤属于优化手段，不是强制约束；过滤后无结果/得分过低，则取消品类过滤重新检索，避免误拒答
    if category and (not hits or hits[0]["rerank_score"] < settings.rerank_min_score):
        hits = await retrieval.search_knowledge(
            query, strategy="hybrid_rerank", category=None, bm25_text=bm25_text
        )

    # -------------------------- 两道证据校验闸（生成回答之前执行，节约LLM生成开销） --------------------------
    # 机械闸：基于重排分数做硬性过滤（无召回或分太低则失败）
    # 仅靠检索相似度，不能代表证据真正能回答问题；此处只做基础门槛拦截
    top = hits[0]["rerank_score"] if hits else 0.0
    if not hits or top < settings.rerank_min_score:
        return {"sufficient": False, "source": "retrieval_low_conf",
                "reason": f"检索证据不足(top={top:.3f})", "citations": []}   # source:检索侧证据不足

    # 语义闸：LLM二次自评证据是否足以回答用户问题
    # 应对风险点：检索高分 ≠ 证据可用，例如退货运费文本相似度高，但无法回答换货运费责任问题
    # 要求自评模型严格依据召回证据，禁止使用自身常识脑补补充信息
    ev_texts = [f"{h['question']} {h['answer']}" for h in hits]
    chk = await selfcheck.check_sufficient(query, ev_texts)
    if not chk["useful"]:
        return {"sufficient": False, "source": "self_check",
                "reason": chk["reason"], "citations": []}   # source:LLM自评证据不足以回答

    # 注：sufficient=False代表当前无法作答；source标记失败分类
    # source枚举：retrieval_low_conf(检索低分) / self_check(自评证据不足) / user_feedback(用户反馈不满意)
    # 本函数仅向外透出状态，问题落库low_confidence_questions表由上层兜底逻辑完成；
    # 落库数据作为数据飞轮输入，用于挖掘需要补充的知识库问题

    # -------------------------- 结果格式化输出 --------------------------
    # 调整文档顺序，缓解大模型lost‑in‑the‑middle问题；该顺序同时作为引用编号顺序
    arranged = retrieval.arrange_head_tail(hits)
    # citations：结构化元数据，供给前端展示跳转，n引用标号与送入大模型的上下文顺序严格对齐
    citations = [
        {"n": i + 1, "id": h["id"], "section_path": h["section_path"],
         "question": h["question"], "answer": h["answer"], "content_type": h["content_type"]}
        for i, h in enumerate(arranged)
    ]
    # evidence：拼接好的纯文本，直接喂给大模型用于生成答案
    evidence = "\n".join(f"[{c['n']}] {c['question']}: {c['answer']}" for c in citations)

    return {"sufficient": True, "evidence": evidence, "citations": citations}


# query_faq 走 RAG 管线(改写+混合检索+重排+自评,多次上游往返):远慢于默认 5s 给 30s;
# 超时多为上游慢而非瞬时抖动,重试大概率仍超时纯烧时间 → max_retries=0(沿 ch05 NO_RETRY 的理由)
registry.register(registry.spec_from_langchain_tool(
    query_faq, source="builtin", timeout=30.0, max_retries=0))
