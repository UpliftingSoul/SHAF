"""
ch09 数据飞轮审核后台 API：提供待审队列列表、缺口详情、审核通过与驳回接口。

审核详情保留归并进该缺口的用户原话及各自召回快照，供人工判断
“知识确实缺失”还是“知识已有但当时没有检到”。

审核通过时，人工核准答案以 QA chunk 复用 ch03 的知识落库流程：
write_pending → vectorize_pending。只有向量化成功后才将审核状态置为「通过」，
保证审核完成后新增知识已经可被后续检索命中（验收 3）。
"""

import logging

from fastapi import APIRouter, HTTPException

from app.db import repository
from app.kb import dualwrite
from app.kb.documents import Chunk, is_key
from app.schemas.review import ApproveRequest

logger = logging.getLogger(__name__)
router = APIRouter()


async def _write_chunks(chunks: list[Chunk]) -> list[int]:
    """复用 ch03 dualwrite 流程，将审核通过的知识块先写入 pending 知识记录。"""

    return await dualwrite.write_pending(chunks)


async def _vectorize() -> int:
    """将 pending 知识完成向量化，使新审核通过的内容进入实际检索链路。"""

    return await dualwrite.vectorize_pending()


def _item_out(r) -> dict:
    """将 review_queue 数据行整理为列表/详情接口共用的基础输出结构。"""

    return {
        "id": r.id,
        "normalized_question": r.normalized_question,
        "ai_suggested_answer": r.ai_suggested_answer,
        "occurrence_count": r.occurrence_count,
        "review_status": r.review_status,
        "created_at": r.created_at.isoformat() if r.created_at else None
    }


@router.get("/api/review/queue")
async def review_queue(status: str | None = None) -> dict:
    """
    返回待审队列，可按审核状态筛选。
    列表按 occurrence_count 降序：同类问题出现越频繁，对应知识缺口越应优先补齐（README）。
    """
    rows = await repository.list_review_queue(status)
    return {"items": [_item_out(r) for r in rows]}


@router.get("/api/review/{review_id}")
async def review_detail(review_id: int) -> dict:
    """
    返回单个知识缺口的审核详情，包括人工核准答案、归并原话及各自召回快照。
    审核人可据此判断是真正缺知识，还是已有知识但检索链路没有命中。
    """
    detail = await repository.get_review_detail(review_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="缺口不存在")

    item, raws = detail
    out = _item_out(item)
    out["approved_answer"] = item.approved_answer

    # retrieved_chunks 保留各原话落池当时的召回现场，是审核“缺知识还是没检到”的直接依据。
    out["raws"] = [
        {"raw_question": r.raw_question, "source": r.source, "reason": r.reason,
         "created_at": r.created_at.isoformat() if r.created_at else None,
         "retrieved_chunks": r.retrieved_chunks}
        for r in raws
    ]
    return out


@router.post("/api/review/{review_id}/approve")
async def approve(review_id: int, req: ApproveRequest) -> dict:
    """
    审核通过：将标准问题与人工核准答案构造成 FAQ Chunk，写入知识库并同步向量化。
    只有知识真正可检索后才把 review_queue 状态改为「通过」；
    写回失败则整体回滚本次 pending 插入，并保持待审以便重试。
    """
    # -------------------- 1. 审核前置校验 --------------------
    # 任何知识写入之前先确认记录存在且仍为待审，避免重复审核直接产生重复知识。
    detail = await repository.get_review_detail(review_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="缺口不存在")

    item, _ = detail
    if item.review_status != "待审":
        raise HTTPException(
            status_code=409,
            detail=f"当前状态为「{item.review_status}」,不可再审"
        )

    # -------------------- 2. 构造并写回核准知识 --------------------
    # 人工答案按普通 FAQ Chunk 进入 ch03 原有落库链路，而不是另造一套飞轮专用知识格式。
    chunk = Chunk(
        category="飞轮沉淀", questions=item.normalized_question,
        answer=req.approved_answer,
        section_path=f"飞轮沉淀 / {item.normalized_question}",
        content_type="faq",
        is_key_clause=is_key(item.normalized_question, req.approved_answer)
    )

    # 顺序必须是 write_pending → vectorize_pending → 更新审核状态：
    # 审核页只有在新增知识已经可检索后，才允许显示为「通过」。
    chunk_ids: list[int] = []
    try:
        chunk_ids = await _write_chunks([chunk])
        await _vectorize()
    except Exception:
        logger.exception(
            "审核写回知识库失败 review=%s(状态保持待审,可重试)", review_id
        )

        if chunk_ids:
            # write_pending 成功但向量化失败时，按 spec 8.2「整体回滚」删除本次 pending。
            # vectorize_pending 会处理全部 pending；残留后再次审核会插入同一知识，造成重复。
            try:
                await repository.delete_knowledge_chunks(chunk_ids)
            except Exception:
                # 回滚失败不掩盖原异常，但必须明确留下“需人工清理”的排障线索。
                logger.exception(
                    "回滚 pending chunk 失败 ids=%s(需人工清理)", chunk_ids
                )

        raise HTTPException(
            status_code=502,
            detail="写回知识库失败(检查嵌入上游/Milvus),状态未变可重试"
        )

    # -------------------- 3. 知识可检索后再提交审核状态 --------------------
    # 此处仍存在并发窗口：KB 已写入，但审核状态可能被另一请求抢先修改。
    # 必须如实返回 409 并说明“知识已写入”，不能伪装成本次操作完整成功。
    if not await repository.update_review_status(
        review_id, "通过", approved_answer=req.approved_answer
    ):
        logger.warning(
            "审核状态更新落空(并发变更?)review=%s,KB 已写入 %s",
            review_id, chunk_ids
        )
        raise HTTPException(
            status_code=409,
            detail="状态已被他人变更;知识已写入,请人工核对"
        )

    logger.info(
        "审核通过 review=%s → knowledge_chunks %s(已向量化,下一问可检索)",
        review_id, chunk_ids
    )
    return {"ok": True, "chunk_ids": chunk_ids}


@router.post("/api/review/{review_id}/reject")
async def reject(review_id: int) -> dict:
    """
    驳回仍处于待审状态的知识缺口。
    驳回属于终审：后续同类问题仍可归并并累计 occurrence_count，但不会自动恢复为待审。
    """
    # update_review_status 只允许合法状态转换；失败后再查一次，用于区分“不存在”和“已不可再审”。
    # update_review_status 返回 True = 状态更新成功；返回 False = 更新失败。
    # ❗先执行驳回操作 update_review_status，然后执行 if not 判断
    if not await repository.update_review_status(review_id, "驳回"):
        detail = await repository.get_review_detail(review_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="缺口不存在")
        raise HTTPException(status_code=409, detail="仅待审状态可驳回")

    return {"ok": True}
