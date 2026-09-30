from typing import Annotated

from langchain_core.tools import InjectedToolArg, tool
from pydantic import Field

from app.tools import registry
from app.tools.builtin.orders import NOT_OWNED
from app.tools.business import owns_order


@tool
async def submit_refund(
    order_id: Annotated[str, Field(description="要退款的订单号")],
    user_id: Annotated[str, InjectedToolArg],
    reason: Annotated[str | None, Field(description="退款原因(可选,最终以前端固定类目下拉为准)")] = None,
) -> dict:
    """
    确认订单符合退款条件后，调用本工具向用户提供退款申请入口。
    本工具不会实际提交退款；真正写入由用户在前端退款表单确认后完成，
    返回“待用户确认”仅表示该订单可以进入退款提交阶段。

    user_id 由系统注入，模型无需也不应主动传入。
    """
    # 二次确认解决的是“用户是否真的要退”，不能替代“订单是否属于该用户”的归属校验；
    # 两者是不同安全边界，因此即使后续还有确认表单，这里仍必须先验订单归属。
    if not owns_order(user_id, order_id):
        return dict(NOT_OWNED)

    return {"status": "待用户确认", "order_id": order_id}


registry.register(registry.spec_from_langchain_tool(
    submit_refund, source="builtin", inject_user_id=True))


# ❗agent_tool & submit refund 联动方面的反直觉设计：
"""
模型看到 submit_refund 工具
        ↓
模型生成 submit_refund(order_id="1001", reason="...")
        ↓
这只是 tool call / 动作提议
        ↓
agent_tools 发现它是 submit_refund
        ↓
不执行 submit_refund() 函数
        ↓
转换成 refund_form suggested_action
        ↓
前端展示确认表单
        ↓
用户确认以后，另外的提交接口才真正写库
"""
