from app.core.llm import get_chat_model
from app.core.prompts import COREF_REWRITE_PROMPT


async def resolve(query: str, history: str = "") -> str:
    """
    指代消解与口语归一：结合最近对话，把依赖上下文的当前话语补全为可独立理解的问题；
    原句本身已完整时保持不变。这里只需要模型返回一句文本，因此不使用结构化输出。
    * 消解属于链路前置增强步骤，任何模型异常都回落原句，不阻断后续意图识别与检索。
    """
    try:
        model = get_chat_model()
        r = await (COREF_REWRITE_PROMPT | model).ainvoke(
            {"query": query, "history": history or "(无)"})
        text = (r.content if isinstance(r.content, str) else "").strip()
    except Exception:
        # 模型调用失败时保留用户原话，让下游仍可继续处理。
        return query

    # 第二层兜底：调用成功但模型返回空内容时，同样退回原始 query。
    return text or query
