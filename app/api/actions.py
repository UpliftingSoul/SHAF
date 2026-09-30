"""
前端用户动作接口。

聊天主流程只负责生成建议动作或暂停点；需要用户明确操作后才能继续的行为由本模块承接：
- create-ticket：用户确认后真正创建工单；
- create-refund：用户提交退款表单后真正写入退款申请；
- resume：把订单选择/确认结果回填给 LangGraph interrupt，并以 SSE 继续原会话。

其中前两个属于实际写操作，resume 本身不创建业务数据，只恢复此前暂停的图执行。
"""

import json
import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.exc import SQLAlchemyError

from app.db import repository
from app.graph import runtime
from app.schemas.actions import (
    CreateRefundRequest,
    CreateRefundResponse,
    CreateTicketRequest,
    CreateTicketResponse,
    ResumeRequest,
)

logger = logging.getLogger(__name__)
router = APIRouter()


def _sse(payload: dict) -> str:
    """将事件字典编码成一个 SSE data 帧，中文内容保持原样输出。"""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.post("/api/actions/create-ticket", response_model=CreateTicketResponse)
async def create_ticket_action(req: CreateTicketRequest) -> CreateTicketResponse:
    """
    建工单动作接口：只有用户明确点击创建后，才复用 ch02 工单能力写入 tickets 表。
    “转人工”是独立的前端动作，不等同于已经创建工单。
    """
    try:
        ticket_no = await repository.create_ticket(
            req.conversation_id, req.description, req.ticket_type)
    except SQLAlchemyError:
        logger.exception("建工单失败 conv=%s", req.conversation_id)
        raise HTTPException(
            status_code=503,
            detail="工单系统暂时不可用,请稍后重试",
        )

    return CreateTicketResponse(ticket_no=ticket_no)


@router.post("/api/actions/create-refund", response_model=CreateRefundResponse)
async def create_refund_action(req: CreateRefundRequest) -> CreateRefundResponse:
    """
    退款表单的真正提交入口：用户确认订单并选择固定退款原因后，
    将申请写入 tickets 表（ticket_type='退款'），复用 ch02 工单能力。
    与 submit_refund 的 tool call 不同，到这里才真正发生退款申请的持久化写入。

   【流程概述】
    submit_refund tool call
        ↓
    agent_tools 拦截
            ↓
    refund_form
            ↓
    用户确认并提交
            ↓
    create_refund_action
            ↓
    repository.create_ticket(...)   ← 到这里才真正写库
    """
    # 落库使用前端最终提交的固定类目原因，而不是模型可能生成的自由文本 reason，
    # 便于后续按退款原因稳定分类和统计。
    desc = f"退款申请 订单号={req.order_id} 原因={req.reason}"

    try:
        ticket_no = await repository.create_ticket(
            req.conversation_id, desc, "退款")
    except SQLAlchemyError:
        logger.exception("退款单创建失败 conv=%s", req.conversation_id)
        raise HTTPException(
            status_code=503,
            detail="退款系统暂时不可用,请稍后重试",
        )

    return CreateRefundResponse(ticket_no=ticket_no)



@router.post("/api/actions/resume")
async def resume_action(req: ResumeRequest):
    """
    LangGraph 中断统一续跑接口，以 SSE 返回恢复后的后续事件。
    ch06 订单选择器回传 order_id；ch08 工单预览卡回传 confirmed，
    两种结果转换成对应的 resume_value 后交给 runtime.stream_resume。

    续跑事件与 /api/chat 同构，前端可复用同一套
    delta / tool / citations / actions / interrupt / done 解析逻辑。
    """
    # 一个端点服务两类中断，但至少必须提供一种恢复结果，否则没有值可回填 interrupt()。
    if req.order_id is None and req.confirmed is None:
        raise HTTPException(
            status_code=400,
            detail="order_id 与 confirmed 至少传一个",
        )

    # Command(resume=...) 的值最终会成为暂停处 interrupt() 的返回值：
    # select_order 接订单号字符串；confirm_ticket 接 {"confirmed": bool}。
    resume_value = (
        req.order_id
        if req.order_id is not None
        else {"confirmed": bool(req.confirmed)}
    )

    async def event_stream() -> AsyncIterator[str]:
        """
        将 runtime 的统一事件 dict 转换成 SSE 帧。
        事件协议与 /api/chat 保持一致，因此前端无需为“新一轮”和“续跑”维护两套解析器。
        """
        try:
            async for ev in runtime.stream_resume(
                    req.conversation_id, resume_value):

                if ev["type"] == "tool":
                    yield _sse({"event": "tool", "name": ev["name"]})

                elif ev["type"] == "delta":
                    yield _sse({"delta": ev["text"]})

                elif ev["type"] == "citations":
                    yield _sse({
                        "event": "citations",
                        "items": ev["items"],
                    })

                elif ev["type"] == "actions":
                    yield _sse({
                        "event": "actions",
                        "items": ev["items"],
                    })

                elif ev["type"] == "interrupt":
                    # 续跑过程中仍可能再次中断，例如 resume 回来的订单号再次校验失败；
                    # 负载原样透传给前端，由 kind 决定展示哪种交互组件。
                    yield _sse({
                        "event": "interrupt",
                        **{k: v for k, v in ev.items() if k != "type"},
                    })

                elif ev["type"] == "done":
                    yield _sse({
                        "event": "done",
                        "conversation_id": ev["conversation_id"],
                    })

        except runtime.ConversationNotFound:
            # SSE 响应此时已经开始，无法再改成普通 HTTP 404；
            # 因此通过 error 事件把失败信息交给前端处理。
            yield "event: error\n"
            yield _sse({"message": "会话不存在"})
            return

        except Exception:
            logger.exception(
                "续跑失败 conv=%s",
                req.conversation_id,
            )
            yield "event: error\n"
            yield _sse({"message": "上游暂时不可用,请稍后重试"})
            return

        # ❗ data: [DONE] 与 runtime 的 done 业务事件 {"event": "done", ...} 不是同一个概念；
        # 一个是 runtime 的业务事件，表示“图这一轮正常跑完”；另一个只是 SSE 这次传输结束的终止标记。
        # 即使本轮因再次 interrupt 而没有 done，当前 SSE 连接仍需要正常结束。
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
    )

