import asyncio
import functools
from concurrent.futures import ThreadPoolExecutor

from pymilvus import (
    AnnSearchRequest, DataType, Function, FunctionType, MilvusClient, RRFRanker,
)

from app.config import settings

COLLECTION = "knowledge"
DIM = 1024

# 专用单线程执行器:同步 pymilvus(gRPC)客户端在多线程间共享会间歇静默返回空
# (async server 用 uvloop + 默认多线程 to_thread 时实测复现)。
# 把所有 Milvus 调用固定到这一个线程(客户端亦在此线程惰性创建),即单线程独占,间歇空返回根治。
_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="milvus")


async def acall(fn, *args, **kwargs):
    """
    在专用单线程执行同步pymilvus调用，适配async业务代码
    解决同步SDK直接调用会阻塞asyncio事件循环、且client非线程安全问题
    """
    # 获取当前正在运行的asyncio事件循环
    loop = asyncio.get_running_loop()
    # partial绑定函数入参，提交给线程池执行同步函数，await等待线程任务完成返回结果
    return await loop.run_in_executor(_EXECUTOR, functools.partial(fn, *args, **kwargs))


async def acall(fn, *args, **kwargs):
    """
    在专用单线程执行同步pymilvus调用，适配async业务代码。
    规避两点问题：①同步SDK阻塞asyncio事件循环；②milvus client非线程安全，多线程共享会间歇性无报错返回空。
    约束：client必须在该专用线程内惰性创建，仅迁移调用无法修复问题。
    """
    # 获取当前asyncio运行事件循环
    loop = asyncio.get_running_loop()
    # partial封装参数，提交单线程池执行同步逻辑，await等待执行完毕
    return await loop.run_in_executor(_EXECUTOR, functools.partial(fn, *args, **kwargs))



_CLIENT: MilvusClient | None = None
_ensured: set[str] = set()

_OUTPUT = ["question", "answer", "section_path", "content_type", "category"]


def get_client(uri: str | None = None) -> MilvusClient:
    """默认返回进程内单例(连 Standalone);传 uri(测试)返回独立客户端。"""
    global _CLIENT
    if uri is not None:
        return MilvusClient(uri=uri)
    if _CLIENT is None:
        _CLIENT = MilvusClient(uri=settings.milvus_uri)
    return _CLIENT


def ensure_collection(client: MilvusClient, collection: str = COLLECTION) -> None:
    """
    【幂等初始化Milvus集合】写入/检索前调用，保证集合存在且已加载完成。
    - 全局默认客户端 _CLIENT 做缓存标记，同一集合仅执行一次初始化；传入其他client、临时测试集合不受缓存约束。
    - 主键id复用MySQL knowledge_chunks.id，auto_id=False保证两端ID一一对应。
    - Schema包含：dense稠密向量(COSINE)、带中文分词text字段、BM25自动生成sparse稀疏向量，及业务标量字段。
    - 不存在则：建表 + 预配置向量索引 + load加载；已存在直接load，重复调用无副作用。
    - 同时构建稠密语义索引+稀疏BM25关键词索引，支持后续混合检索。
    """
    if client is _CLIENT and collection in _ensured:
        return
    if client.has_collection(collection):
        # 集合已存在：Milvus检索前必须load把数据加载到内存，否则无法查询
        client.load_collection(collection)

    else:
        # 最重要的是 auto_id=False：主键不让 Milvus 自己生成，直接用 MySQL 那边 knowledge_chunks.id 的值，
        # 两个库共享同一个身份。这一个决定换来的是天然幂等
        schema = client.create_schema(auto_id=False)

        schema.add_field("id", DataType.INT64, is_primary=True)  # 主键，和MySQL知识库chunk id完全一致
        schema.add_field("dense", DataType.FLOAT_VECTOR, dim=DIM)  # 稠密向量，文本Embedding结果，用于语义相似度检索
        # text = category+questions+answer 拼接,恒长于answer(8192)；开启中文分词；放宽到 16384 防长块 upsert 溢出
        # 【补充】enable_analyzer+chinese分析器必须同时开启；默认仅按标点空格切词，中文分词失效会导致BM25结果无效
        schema.add_field("text", DataType.VARCHAR, max_length=16384,
                         enable_analyzer=True, analyzer_params={"type": "chinese"})
        # 【补充】业务代码无需手动赋值sparse；由Milvus BM25内置函数自动分词计算填充
        schema.add_field("sparse", DataType.SPARSE_FLOAT_VECTOR)  # 稀疏向量，由BM25函数自动生成，不手动填值
        schema.add_field("question", DataType.VARCHAR, max_length=2048)  # 问题字段，元数据过滤
        schema.add_field("answer", DataType.VARCHAR, max_length=8192)  # 原始回答内容
        schema.add_field("section_path", DataType.VARCHAR, max_length=512)  # 文档章节路径，用于过滤溯源
        schema.add_field("content_type", DataType.VARCHAR, max_length=32)  # 内容类型标记
        schema.add_field("category", DataType.VARCHAR, max_length=255)  # 业务分类标签

        # BM25内置函数：输入text字段，自动计算输出sparse稀疏向量，实现关键词检索
        # 【补充】Milvus内置能力：写入阶段自动完成分词、BM25权重计算，应用层只需要写入原始text文本
        schema.add_function(Function(
            name="text_bm25", input_field_names=["text"],
            output_field_names=["sparse"], function_type=FunctionType.BM25,
        ))

        # index_params：索引配置对象，Milvus向量字段**必须建立索引才能做检索**
        # 【补充】同一集合挂载两套独立索引：dense语义检索、sparse关键词检索，支持后续混合检索hybrid
        index_params = client.prepare_index_params()
        # 稠密向量索引：AUTOINDEX自动选合适索引，COSINE余弦相似度衡量语义距离
        index_params.add_index(field_name="dense", index_type="AUTOINDEX", metric_type="COSINE")
        # 稀疏向量索引：倒排索引，适配BM25关键词打分
        index_params.add_index(field_name="sparse", index_type="SPARSE_INVERTED_INDEX", metric_type="BM25")

        # 真正创建集合：传入表结构schema+索引配置，一次性建好表和索引
        client.create_collection(collection, schema=schema, index_params=index_params)
        # 新建集合同样需要加载进内存，才能后续读写查询
        client.load_collection(collection)

    # 标记：默认客户端下此集合已完成初始化，下次直接短路跳过
    if client is _CLIENT:
        _ensured.add(collection)


def upsert_vectors(client: MilvusClient, rows: list[dict], collection: str = COLLECTION) -> None:
    if rows:
        client.upsert(collection, rows)


def flush(client: MilvusClient, collection: str = COLLECTION) -> None:
    """刷盘:新 upsert 的数据须 flush 后才可被 BM25/检索命中(Task 1 实证)。"""
    client.flush(collection)


def _cat_expr(category: str | None) -> str:
    """
    构造Milvus过滤布尔表达式，用于category品类条件下推至数据库索引层过滤。
    ✅优势：Milvus检索阶段直接剔除不匹配数据；避免Python上层过滤导致top_k名额被无效数据占用，返回结果数量不足。
    :param category: 业务分类，None返回空字符串代表不过滤
    :return: milvus filter表达式字符串
    """
    return f'category == "{category}"' if category else ""



def _hit(h: dict) -> dict:
    e = h["entity"]
    return {"id": h["id"], "score": float(h["distance"]),
            "question": e["question"], "answer": e["answer"],
            "section_path": e["section_path"], "content_type": e["content_type"],
            "category": e["category"]}


def dense_search(client, vector, top_k, category=None, collection=COLLECTION) -> list[dict]:
    res = client.search(collection, data=[vector], anns_field="dense", limit=top_k,
                        output_fields=_OUTPUT, search_params={"metric_type": "COSINE"},
                        filter=_cat_expr(category))
    # Standalone COSINE 的 distance 即相似度(越大越相似),与 Lite 的「1-相似度」不同
    return [_hit(h) for h in res[0]]


def bm25_search(client, text, top_k, category=None, collection=COLLECTION) -> list[dict]:
    res = client.search(collection, data=[text], anns_field="sparse", limit=top_k,
                        output_fields=_OUTPUT, search_params={"metric_type": "BM25"},
                        filter=_cat_expr(category))
    return [_hit(h) for h in res[0]]


def hybrid_search(client, vector, text, top_k, recall=50, category=None, collection=COLLECTION) -> list[dict]:
    """
    两个请求各打各的字段， RRFRanker 把两份排名融成一份。
    * BM25 分是个没有上界的实数，两者量纲完全不可比，加权求和这条路走不通。
    """
    expr = _cat_expr(category)
    dense_req = AnnSearchRequest(data=[vector], anns_field="dense",
                                 param={"metric_type": "COSINE"}, limit=recall, expr=expr)
    sparse_req = AnnSearchRequest(data=[text], anns_field="sparse",
                                  param={"metric_type": "BM25"}, limit=recall, expr=expr)
    res = client.hybrid_search(collection, reqs=[dense_req, sparse_req], ranker=RRFRanker(),
                               limit=top_k, output_fields=_OUTPUT)
    return [_hit(h) for h in res[0]]


def count(client, collection: str = COLLECTION) -> int:
    return client.query(collection, filter="id >= 0", output_fields=["count(*)"])[0]["count(*)"]


def drop(client, collection: str) -> None:
    if client.has_collection(collection):
        client.drop_collection(collection)
