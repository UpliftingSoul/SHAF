from typing import Literal

from pydantic import BaseModel, Field

from app.config import settings
from app.core import llm
from app.core.llm import get_chat_model
from app.core.prompts import INTENT_CLASSIFY_PROMPT

INTENTS = ("物流", "订单", "商品咨询", "退款退货", "售后", "投诉", "人工", "闲聊", "其他")


class _Intent(BaseModel):
    intent: Literal["物流", "订单", "商品咨询", "退款退货", "售后", "投诉", "人工", "闲聊", "其他"] = Field(
        description="九类意图之一")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0, description="判断把握 0-1")


async def classify(query: str, history: str = "") -> dict:
    """九类意图 + confidence(ch08 增「人工」:明确要求建工单/转人工)。扁平字段避开 glm 嵌套 502;
    解析失败/越界 → 归「其他」(最保守兜底,替换 ch05 的回退闲聊)。
    模型默认走 settings.intent_model(空则回落 chat_model)——先求准。"""
    # settings.intent_model or None 这个回落留了个口子：意图分类可以单独指定模型，不配就跟着聊天模型走。
    # 宁可配个更够格的大模型也别在分流上判飞
    model = llm.structured(_Intent, model=settings.intent_model or None)

    # schema 约束了，不代表模型一定守约，所以还有一道 in INTENTS 的二次校验
    # 整个调用包在 try 里，任何异常都归「其他」并把 confidence 记 0，让下游能看出这是个兜底而不是真判断。
    try:
        r: _Intent = await (INTENT_CLASSIFY_PROMPT | model).ainvoke(
            {"query": query, "history": history or "(无)"})
    except Exception:
        return {"intent": "其他", "confidence": 0.0}
    intent = r.intent if r.intent in INTENTS else "其他"
    return {"intent": intent, "confidence": float(r.confidence)}
