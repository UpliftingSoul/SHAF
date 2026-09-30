from app.core import embeddings
from app.db import repository
from app.kb import milvus_client
from app.kb.documents import Chunk


async def write_pending(chunks: list[Chunk]) -> list[int]:
    """按顺序把一份文档的 chunks 写入 MySQL(status=pending),并在同文档内连 prev/next 指针。"""
    ids: list[int] = []
    for c in chunks:
        cid = await repository.insert_knowledge_chunk(
            c.category, c.questions, c.answer,
            section_path=c.section_path, content_type=c.content_type,
            is_key_clause=c.is_key_clause,
        )
        ids.append(cid)
    for i, cid in enumerate(ids):
        prev_id = ids[i - 1] if i > 0 else None
        next_id = ids[i + 1] if i < len(ids) - 1 else None
        await repository.set_chunk_neighbors(cid, prev_id, next_id)
    return ids


def _batches(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


async def vectorize_pending(client=None, batch_size: int = 64, collection: str = milvus_client.COLLECTION) -> int:
    """
    【幂等可重跑】待处理知识库块批量向量化写入Milvus
    流程：读取pending待处理记录 → 拼接文本向量化 → 组装Milvus行数据 → upsert写入向量库 → MySQL标记已完成
    - client为空时，在Milvus专用线程惰性创建客户端；通过acall隔离线程，规避async服务下gRPC跨线程共享异常
    - upsert基于MySQL主键id覆盖，支持中断后重跑，不会重复处理
    :return: 本次成功处理的chunk总数
    """
    # 从MySQL查询状态为pending、等待向量化的知识库分片
    pending = await repository.list_pending_chunks()
    done = 0

    # 按batch_size分批处理，避免单次数据量过大
    for batch in _batches(pending, batch_size):
        # 拼接用于向量&BM25检索的文本：category + questions + answer；其余字段仅作元数据过滤溯源
        texts = [f"{r.category}\n{r.questions}\n{r.answer}" for r in batch]
        # 调用Embedding模型生成稠密向量dense
        vectors = await embeddings.embed_texts(texts)

        # 组装Milvus写入行：id复用MySQL主键；sparse由Milvus BM25函数内部自动生成，业务无需传入
        rows = [
            {"id": r.id, "dense": v, "text": t,
             "question": r.questions, "answer": r.answer,
             "section_path": r.section_path or "", "content_type": r.content_type or "",
             "category": r.category or ""}
            for r, v, t in zip(batch, vectors, texts)
        ]

        # 交给Milvus后台线程执行：保证集合就绪 + upsert新增/覆盖向量记录
        def work_upsert(rows=rows):
            c = client or milvus_client.get_client()
            milvus_client.ensure_collection(c, collection=collection)
            milvus_client.upsert_vectors(c, rows, collection=collection)

        await milvus_client.acall(work_upsert)

        # MySQL更新状态为已向量化；upsert按id覆盖实现幂等，程序中断重跑不会重复处理已完成批次
        for r in batch:
            await repository.mark_chunk_vectorized(r.id, str(r.id))
        done += len(batch)

    if done:
        # 批量处理完毕执行刷盘，确保新写入数据对BM25/向量检索可见
        def work_flush():
            c = client or milvus_client.get_client()
            milvus_client.flush(c, collection=collection)

        await milvus_client.acall(work_flush)

    return done
