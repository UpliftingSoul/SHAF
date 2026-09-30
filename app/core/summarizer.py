"""
ch07 后台分段摘要：轮末按 Layer 2 的 token 用量触发，异步执行，不阻塞当轮回复。

三层上下文中，Layer 1 保留近期原文，Layer 2 承接降级后的半压历史；
当 Layer 2 超出自身预算时，只将尚未摘要的这批历史压成新段落，追加到 conversation_summaries，不再反复重写已有摘要。
注入上下文时仅使用最近若干段，兼顾历史信息保留与 token 开销。

调度使用进程内 _running 防止同一会话重复启动任务；
摘要失败只记录日志、不推进边界，后续轮次仍可再次触发。
"""

import asyncio
import logging
import time

from pydantic import BaseModel, Field

from app.config import settings
from app.core import budget, llm, memory
from app.core.llm import get_chat_model
from app.core.prompts import SUMMARY_PROMPT
from app.db import repository

logger = logging.getLogger(__name__)

# 当前进程内正在执行的摘要任务：conversation_id -> Task，同一会话在跑时不重复启动。
_running: dict[int, asyncio.Task] = {}


class _Summary(BaseModel):
    """摘要模型的结构化输出，只接收本批新生成的摘要文本。"""

    summary: str = Field(description="早期对话滚动摘要,只含事实与诉求")


async def summarize_dialog(old_summary: str, dialog: str) -> str:
    """
    将本批待压缩对话生成一段独立摘要，使用结构化输出约束返回格式。
    old_summary 仅作为历史背景，dialog 是本批新增内容；已有段落不参与重写。
    提示词也须明确禁止复述、合并旧摘要，避免分段存储后内容仍重复累积。
    """
    model = llm.structured(_Summary, slot="summary")
    r: _Summary = await (SUMMARY_PROMPT | model).ainvoke(
        {"old_summary": old_summary or "(无)", "dialog": dialog})
    return (r.summary or "").strip()


async def run_summary(conversation_id: int) -> None:
    """
    ch07 执行一次后台分段摘要：读取会话与消息，以 Layer 1 起点确定摘要边界，
    仅压缩尚未被摘要覆盖的 Layer 2 内容，成功后追加新段落并推进摘要边界。
    异常向外抛，由调度任务的 done callback 统一记录。
    """
    t0 = time.monotonic()
    conv = await repository.get_conversation(conversation_id)
    if conv is None:
        return

    # 取对话消息（user + assistant）
    msgs = await repository.list_dialog_messages(conversation_id)
    old_upto = conv.summary_upto_msg_id or 0

    # ch07 三层策略：摘要只接收 Layer 2，直接以 Layer 1 起点作为本次覆盖上界。
    # 不再沿用旧版按最近 K 轮计算边界的规则，否则可能将仍在 Layer 1 的原文提前摘要。
    boundary = conv.layer1_from_msg_id or 0
    if boundary <= old_upto:
        logger.info("summary skip conv=%s 层2 是空的(边界=%s upto=%s)",
                    conversation_id, boundary, old_upto)
        return

    # 只取 (old_upto, boundary]：已经进入旧摘要的消息不重复压缩，
    # 既减少模型调用开销，也避免相同历史反复摘要造成事实细节漂移。
    seg = [m for m in msgs if old_upto < m.id <= boundary]
    dialog = "\n".join(
        f"{'用户' if m.role == 'user' else '客服'}:{m.content}" for m in seg if m.content)

    logger.info("summary start conv=%s msgs=%s upto %s->%s",
                conversation_id, len(seg), old_upto, boundary)

    # 旧摘要仅供模型理解历史背景，本次产出的是独立新段，而非重新生成完整滚动摘要。
    summary = await summarize_dialog(conv.summary or "", dialog)

    # 空摘要按失败处理：不能写入空段并推进边界，否则本批原文退出滑窗后将失去摘要承接。
    if not summary:
        raise ValueError("摘要为空,放弃更新")

    # ch07 分段持久化：追加新段，并同步更新会话的摘要边界及最近摘要投影。
    # from_msg_id 从旧边界的下一条开始，使各段覆盖区间连续且互不重叠。
    n = await repository.append_summary_segment(
        conversation_id, from_msg_id=old_upto + 1, upto_msg_id=boundary, content=summary)

    logger.info("summary done conv=%s 第%s段 upto=%s len=%s cost=%.0fms",
                conversation_id, n, boundary, len(summary), (time.monotonic() - t0) * 1000)


async def _should_summarize(conversation_id: int, conv) -> bool:
    """
    判断是否需要启动摘要：仅当 Layer 2 的估算 token 用量超过自身预算时返回 True。
    Layer 2 由摘要边界与 Layer 1 起点夹定；预算取总滑窗中未分配给 Layer 1 的部分。
    不再沿用旧版新增消息数阈值，避免刚降级的历史立即被摘要、使中间层形同虚设。
    """
    upto = conv.summary_upto_msg_id or 0
    layer1_from = conv.layer1_from_msg_id or 0

    if layer1_from <= upto:  # Layer 2 为空，没有待压缩历史
        return False

    # 按实际文本长度估算用量，而不是按消息条数触发。
    # 数据库仅保存 user / assistant 消息；工具结果不在此统计范围内。
    msgs = await repository.list_dialog_messages(conversation_id)
    layer2_tok = memory.chars_to_tokens(
        sum(len(m.content or "") for m in msgs if upto < m.id <= layer1_from))
    l2_budget = int(budget.compute().sliding * (1 - settings.layer1_ratio))

    if layer2_tok <= l2_budget:
        return False

    logger.info("summary trigger conv=%s 层2 约%s token > 预算 %s",
                conversation_id, layer2_tok, l2_budget)
    return True


async def maybe_schedule_summary(conversation_id: int) -> None:
    """
    ch07 轮后摘要调度入口：Layer 2 超预算且当前会话没有摘要任务在运行时，
    通过 create_task 后台启动 run_summary，不等待摘要完成，因此不会阻塞当轮回复。

    本函数位于正常回复链路上，调度检查异常只记录日志、不影响用户回复；
    摘要失败时边界不会推进，触发条件仍在，后续轮次可以再次尝试。
    """
    try:
        if conversation_id in _running:
            return

        conv = await repository.get_conversation(conversation_id)
        if conv is None:
            return
        if not await _should_summarize(conversation_id, conv):
            return

        # 上面的 await 会暂时让出执行权；在此期间，同一会话的另一协程可能已经启动了摘要任务。
        # 因此真正创建任务前再检查一次，避免两个并发触发重复启动同一会话的摘要。
        if conversation_id in _running:
            return

        # 创建摘要后台任务并登记到 _running，但不等待它执行完成：
        # run_summary 由事件循环继续调度，当前回复流程可以直接往下结束。
        task = asyncio.create_task(run_summary(conversation_id))
        _running[conversation_id] = task

        def _done(t: asyncio.Task) -> None:
            """
            后台摘要任务结束时自动执行的回调：清理运行标记，并记录任务异常。

            执行流程：
            create_task 启动摘要 → run_summary 在后台执行 → 任务结束 → 自动调用 _done(task)。
            """
            # 只有 _running 中登记的仍是当前任务时才清除，
            # 避免旧任务结束较晚，误删同一会话后来登记的新任务。
            if _running.get(conversation_id) is t:
                _running.pop(conversation_id, None)

            # create_task 中发生的异常不会进入当前回复流程的 try/except；
            # 因此任务结束后主动读取异常并记录，避免后台失败没有日志留痕。
            if not t.cancelled() and t.exception() is not None:
                logger.error("summary failed conv=%s",
                             conversation_id, exc_info=t.exception())

        # 注册完成回调：任务成功、失败或取消后，事件循环都会调用 _done(task)。
        task.add_done_callback(_done)

    except Exception:
        # 调度检查失败不应破坏已经正常完成的用户回复；
        # 摘要条件仍然存在，后续轮次可以重新触发。
        logger.exception("summary trigger check failed conv=%s", conversation_id)
