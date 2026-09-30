"""
聊天接口请求 Schema。

定义单轮聊天请求的基础字段，并在请求进入业务链路前校验用户消息长度。
输入上限与上下文预算中的 max_user_input_tokens 保持一致，避免超长单轮消息
突破预算假设、直接挤爆模型上下文窗口。
"""

from pydantic import BaseModel, Field, field_validator

from app.config import settings
from app.core import memory


class ChatRequest(BaseModel):
    """聊天请求参数：标识用户与会话，并对本轮用户输入执行长度前置校验。"""

    user_id: str = Field(min_length=1, description="用户标识,用于建/归属会话")
    message: str = Field(min_length=1, description="用户本轮消息")
    conversation_id: int | None = Field(
        default=None, description="续接会话时带上;为空则新建会话,由 done 帧回传新 id"
    )

    @field_validator("message")
    @classmethod
    def _within_input_budget(cls, v: str) -> str:
        """
        校验单轮用户输入是否超过配置的 token 上限。
        复用 memory.count_tokens() 与上下文管理保持同一统计口径；
        超限时在请求入口直接拒绝，避免破坏后续上下文预算并挤爆模型窗口。
        """

        limit = settings.max_user_input_tokens

        # 按真实 HumanMessage 形态估算 token，而不是另写字符换算规则，
        # 确保这里的输入闸与滑窗、预算计算使用同一套计数口径。
        if memory.count_tokens([("human", v)]) > limit:
            raise ValueError(
                f"这条消息太长了(上限约 {memory.tokens_to_chars(limit)} 字),"
                f"麻烦分几次说,或者只留关键信息"
            )

        return v
