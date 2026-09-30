import logging

from pydantic import BaseModel, Field

from app.core import llm
from app.core.llm import get_chat_model
from app.core.prompts import EXPAND_QUERIES_PROMPT, QUERY_REWRITE_PROMPT

logger = logging.getLogger(__name__)


class _Rewrite(BaseModel):
    """
    两个产物分工不同。
    standard 是去掉口语和情绪的标准问法，「我买的这破玩意儿到底啥时候能到啊」变成「订单配送时效是多久」。
    expanded 是三到六个同义词，【邮费】和【运费】、【多久到】和【时效】这种。
    """
    standard: str = Field(description="标准问法")
    expanded: list[str] = Field(default_factory=list, description="同义/近义扩展词")


class _Expanded(BaseModel):
    queries: list[str] = Field(default_factory=list, description="严格3条检索友好查询")


async def understand(query: str) -> dict:
    """用户口语查询标准化改写 + 同义问句扩展，优化检索召回效果。
    使用扁平结构体输出规避模型嵌套结构报错，防止GLM-5.2触发502异常。
    容错降级策略：改写异常/模型输出空值时，自动回落用户原始输入，保证检索链路不中断。
    """
    # 初始化结构化输出模型，限定输出格式
    model = llm.structured(_Rewrite)
    try:
        # 执行查询改写：规整口语、生成同义扩展词
        r: _Rewrite = await (QUERY_REWRITE_PROMPT | model).ainvoke({"query": query})
    except Exception:
        # 改写失败降级：保留原查询，不阻断整体检索流程
        logger.warning("query 改写失败,回落原问题 query=%r", query, exc_info=True)
        return {"standard": query, "expanded": []}

    # 兜底判空：避免模型输出空标准问导致检索失效
    standard = r.standard or query
    # 兜底：模型返回None则置空列表，确保为列表类型，供BM25关键词召回使用
    expanded = list(r.expanded or [])
    return {"standard": standard, "expanded": expanded}


async def expand_queries(query: str) -> list[str]:
    """
    将单个问题泛化为最多 3 条可独立检索的查询，供 refund_flow 的 retrieve_policy 多路召回。
    使用 JSON 单字段的扁平 list[str] 结构，规避 glm 复杂结构化输出导致的 502；
    模型异常或结果全空时回落 [query]，保证退款检索至少始终有一条查询可执行。

    该重扩写仅用于退款核心链路；商品咨询仍使用 understand 的轻量改写/同义词扩展。
    """
    model = llm.structured(_Expanded)

    try:
        r: _Expanded = await (
            EXPAND_QUERIES_PROMPT | model
        ).ainvoke({"query": query})
        qs = [q.strip() for q in (r.queries or []) if q and q.strip()]
    except Exception:
        qs = []

    # 严格限制前三条有效 query；扩写失败时退回原问题，不让增强步骤阻断退款链路。
    return qs[:3] or [query]
