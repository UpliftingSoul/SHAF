"""
ch09 数据飞轮入口3：接收用户满意度反馈。

👍 仅记录日志，不进入低置信池；👎 将用户问题以 source=user_feedback 落池，
并尽力从该会话最近一轮 checkpoint 回捞当时的召回快照。
只有 checkpoint 中的问题与被点踩问题一致时才复用快照，避免跨轮错配；
快照回捞失败不阻断问题入池，用户原话始终是必须保留的核心信息。
"""

import logging

from fastapi import APIRouter

from app.db import repository
from app.graph import runtime
from app.schemas.feedback import FeedbackRequest, FeedbackResponse

logger = logging.getLogger(__name__)
router = APIRouter()


def _same_question(a: str, b: str) -> bool:
    """忽略空白差异比对问题文本，用于确认最近 checkpoint 确实属于被反馈的那一轮。"""

    return "".join(a.split()) == "".join(b.split())


@router.post("/api/feedback", response_model=FeedbackResponse)
async def submit_feedback(req: FeedbackRequest) -> FeedbackResponse:
    """
    处理单条满意度反馈：👍 只记日志；👎 尽力回捞同轮召回快照后写入低置信池。
    快照属于辅助审核信息，回捞异常时仍保证用户问题正常落池。
    """
    # 正向反馈没有明确的后续修复动作，只做观测留痕，不进入数据飞轮问题池。
    if req.rating == "up":
        logger.info("feedback up conv=%s q=%r(只记日志,不落库)",
                    req.conversation_id, req.question[:40])
        return FeedbackResponse(pooled=False)

    snapshot = None

    try:
        # checkpoint 保存的是该会话最近一轮终态，而用户可能点踩更早的一条答复；
        # 必须先核对问题文本，宁可不带快照，也不能把别轮召回结果错配到当前问题。
        turn = await runtime.get_turn_snapshot(req.conversation_id)
        if turn["snapshot"] and _same_question(turn["question"], req.question):
            snapshot = turn["snapshot"]

    except Exception:
        # 快照只是审核辅助信息；回捞失败不能影响最重要的用户原话进入低置信池。
        logger.warning("feedback 快照回捞失败 conv=%s(照常落池)",
                       req.conversation_id, exc_info=True)

    await repository.insert_low_confidence(
        req.conversation_id, req.question, "user_feedback", "用户反馈未解决",
        retrieved_chunks=snapshot,
    )

    logger.info("feedback down conv=%s 已落池 快照=%s",
                req.conversation_id, "有" if snapshot else "无")

    return FeedbackResponse(pooled=True)
