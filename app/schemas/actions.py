"""
前端用户动作相关的请求/响应模型。

这里承接聊天过程中需要用户明确操作后才能继续的动作：
建工单、提交退款表单，以及恢复 LangGraph interrupt 的订单选择/确认结果。
"""

from typing import Literal

from pydantic import BaseModel, Field


class CreateTicketRequest(BaseModel):
    """创建工单请求：由用户确认操作后提交实际工单数据。"""

    conversation_id: int
    description: str = Field(min_length=1)
    ticket_type: Literal["售后", "投诉", "咨询"]


class CreateTicketResponse(BaseModel):
    """工单成功创建后的返回结果。"""

    ticket_no: str
    status: str = "已转人工"


class CreateRefundRequest(BaseModel):
    """
    前端退款表单的最终提交数据。
    reason 只接受固定业务类目，真正落库以用户在表单中的选择为准，
    不直接采用模型 tool call 中可能生成的自由文本原因。
    """

    conversation_id: int
    order_id: str = Field(min_length=1)
    reason: Literal["七天无理由", "质量问题", "发错货", "不想要了", "其他"]


class CreateRefundResponse(BaseModel):
    """退款申请成功落单后的返回结果。"""

    ticket_no: str
    status: str = "退款申请已提交"


class ResumeRequest(BaseModel):
    """
    统一的中断续跑请求：
    ch06 订单选择器回传 order_id；ch08 工单预览卡回传 confirmed。
    接口层再将两种输入转换成对应形状的 resume_value。
    """

    conversation_id: int
    order_id: str | None = Field(default=None, min_length=1)  # ch06：订单选择器点选结果
    confirmed: bool | None = None                             # ch08：工单预览确认/取消结果

