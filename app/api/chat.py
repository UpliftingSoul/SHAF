import json
import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from sqlalchemy.exc import SQLAlchemyError

from app.graph import runtime
from app.schemas.chat import ChatRequest

logger = logging.getLogger(__name__)
router = APIRouter()


# 封装SSE普通消息帧，ensure_ascii=False保留中文不转义；\n\n为协议消息结束标记
def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


# 生成SSE error事件帧，供前端监听错误事件
async def _sse_error(message: str) -> AsyncIterator[str]:
    yield "event: error\n"
    yield _sse({"message": message})


@router.post("/api/chat")
async def chat(req: ChatRequest):
    # 内部异步生成器：逐步产出SSE消息片段
    async def event_stream() -> AsyncIterator[str]:
        try:
            # 迭代大模型流式输出片段
            async for ev in runtime.stream_turn(req.user_id, req.message, req.conversation_id):
                if ev["type"] == "tool":
                    yield _sse({"event": "tool", "name": ev["name"]})
                elif ev["type"] == "delta":
                    yield _sse({"delta": ev["text"]})
                elif ev["type"] == "citations":
                    yield _sse({"event": "citations", "items": ev["items"]})
                elif ev["type"] == "interrupt":
                    # ch08:负载透传(select_order 带 orders / confirm_ticket 带 preview),前端按 kind 分发
                    yield _sse({"event": "interrupt",
                                **{k: v for k, v in ev.items() if k != "type"}})
                elif ev["type"] == "actions":
                    yield _sse({"event": "actions", "items": ev["items"]})
                elif ev["type"] == "done":
                    yield _sse({"event": "done", "conversation_id": ev["conversation_id"]})

        # 流式接口特有的约束值得记住：错误只能以帧的形式发出去，不能抛 HTTP 500 。
        # 响应头在第一帧发出去的那一刻就定了，之后再想改状态码已经来不及，所以前端得靠 event: error 这一帧知道出事了
        except runtime.ConversationNotFound:
            # 会话不存在，推送SSE错误事件
            for f in _sse_error("会话不存在"):
                yield f
            return
        except SQLAlchemyError:
            # 数据库异常，记录日志，对外屏蔽内部细节
            logger.exception("数据库错误 user_id=%s", req.user_id)
            for f in _sse_error("数据库暂时不可用,请稍后重试"):
                yield f
            return
        except Exception:
            # 全局兜底捕获，记录完整异常堆栈
            logger.exception("图编排失败 user_id=%s", req.user_id)
            for f in _sse_error("上游模型暂时不可用,请稍后重试"):
                yield f
            return
        # 正常流结束标记，通知前端对话完成
        yield "data: [DONE]\n\n"

    # 将异步生成器包装为SSE流式响应，边生产边推送给前端，实现打字机效果
    return StreamingResponse(event_stream(), media_type="text/event-stream")
