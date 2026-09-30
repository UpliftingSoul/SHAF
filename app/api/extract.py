import logging

from fastapi import APIRouter, Depends, HTTPException
from langchain_core.runnables import Runnable

from app.core.llm import get_chat_model
from app.core.prompts import EXTRACT_PROMPT
from app.schemas.extract import AfterSalesTicket, ExtractRequest

logger = logging.getLogger(__name__)
router = APIRouter()

# with_structured_output 把售后描述 pydantic 模型变成对模型输出的约束，管道符把它和提示词模板串成一条可调用的链。
def get_extractor() -> Runnable:
    model = get_chat_model()
    return EXTRACT_PROMPT | model.with_structured_output(AfterSalesTicket)


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

