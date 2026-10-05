"""售后信息提取 API：通过统一结构化通路解析工单，校验输入并隔离上游错误。"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from langchain_core.runnables import Runnable

from app.core import llm
from app.core.prompts import EXTRACT_PROMPT
from app.schemas.extract import AfterSalesTicket, ExtractRequest

logger = logging.getLogger(__name__)
router = APIRouter()

# 统一绑定 Pydantic schema，避免依赖 SDK 默认输出方式及各上游兼容差异。
def get_extractor() -> Runnable:
    return EXTRACT_PROMPT | llm.structured(AfterSalesTicket)


@router.post("/api/extract", response_model=AfterSalesTicket)
async def extract(
    req: ExtractRequest,
    # 依赖注入获取结构化提取链路，每次请求新建实例；牺牲少量对象构造开销，便于单元测试直接替换逻辑，无需打补丁
    extractor: Runnable = Depends(get_extractor)
) -> AfterSalesTicket:
    """
    /api/extract 接口
    功能：接收用户原始文本，调用大模型链路，结构化输出售后工单对象 AfterSalesTicket
    """
    try:
        # 异步执行文本结构化解析
        return await extractor.ainvoke({"text": req.text})
    except Exception as exc:
        # 记录完整异常堆栈用于内部排查；上游报错包含内部地址、模型标识，不能直接返回给前端，防止信息泄露
        logger.exception("结构化提取失败")
        # 对外返回友好错误提示，状态码502代表上游模型服务异常
        raise HTTPException(status_code=502, detail="上游模型暂时不可用，请稍后重试") from exc

