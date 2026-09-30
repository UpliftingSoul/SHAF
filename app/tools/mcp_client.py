"""
ch08 · MCP Client。

通过 MultiServerMCPClient 接入多个独立 MCP Server，并在每次获取工具清单时动态拉取：
Server 新增或下线工具后，客服系统无需维护静态工具清单即可感知变化。

MCP Server 负责声明工具名称、用途和参数；权限与结果格式化仍由客服系统自身控制：
权限统一由 registry 判定，已登记 formatter 的工具在客户端侧挑选必要字段并翻译内部枚举，未登记的 MCP 工具则原样透传。
"""

import asyncio
import logging

from langchain_mcp_adapters.client import MultiServerMCPClient

from app.config import settings
from app.tools import registry
from app.tools.registry import ToolSpec

logger = logging.getLogger(__name__)


def _translate(mapping: dict[str, str], code):
    """将已知内部枚举翻译为业务文本；未知值保留原码，避免信息被静默丢弃。"""
    return mapping.get(code, code)


def _fmt_logistics(d: dict) -> dict:
    """保留客服回答需要的物流字段，并将内部物流状态码翻译成人话。"""
    return {
        "tracking_no": d.get("tracking_no"),
        "status": _translate(
            {"PICKED_UP": "已揽件", "IN_TRANSIT": "运输中",
             "DELIVERING": "派送中", "DELIVERED": "已签收"},
            d.get("status_code"),
        ),
        "current_city": d.get("current_city"),
        "trace": d.get("trace"),
    }


def _fmt_warranty(d: dict) -> dict:
    """保留保修查询所需字段，并隐藏 policy_ref 等客服回答不需要的内部信息。"""
    return {
        "order_id": d.get("order_id"),
        "warranty": _translate(
            {"IN_WARRANTY": "在保", "EXPIRED": "已过保"},
            d.get("warranty_code"),
        ),
        "warranty_until": d.get("warranty_until"),
    }


def _fmt_return(d: dict) -> dict:
    """整理退货进度字段，并把内部状态码转换成用户可理解的状态。"""
    return {
        "order_id": d.get("order_id"),
        "return_status": _translate(
            {"AUDITING": "审核中", "RETURNING": "退货中",
             "REFUNDED": "已退款", "NONE": "无退货记录"},
            d.get("return_code"),
        ),
        "updated_at": d.get("updated_at"),
    }


# ch08：结果治理由客服侧登记，而不是交给外部 Server 决定。
# 已登记工具挑选必要字段并翻译内部枚举（format_result回调实现）；未登记的 MCP 工具由 engine 原样透传。
FORMATTERS = {
    "query_logistics": _fmt_logistics,
    "query_warranty": _fmt_warranty,
    "query_return_status": _fmt_return,
}


_client: MultiServerMCPClient | None = None


def _connections() -> dict:
    """集中声明当前接入的 MCP Server 名称、传输方式和地址。"""
    return {
        "logistics": {"transport": "streamable_http", "url": settings.mcp_logistics_url},
        "aftersales": {"transport": "streamable_http", "url": settings.mcp_aftersales_url},
    }


def get_client() -> MultiServerMCPClient:
    """
    获取进程内复用的 MCP Client。
    handle_tool_errors=False 让真实工具异常继续向外抛出，
    后续统一交给 ch08 engine 做重试、故障分诊、ToolMessage 回灌与审计。

    【异常处理】
    MCP Server 抛异常
            ↓
    MCP adapter 不吞
            ↓
    engine.execute_tool_call()
            ↓
    _TRANSIENT 判断 / 重试 / 分诊
            ↓
    ToolMessage 回灌 + tool_audit_logs
    """
    global _client
    if _client is None:
        _client = MultiServerMCPClient(_connections(), handle_tool_errors=False)
    return _client


async def _get_tools_of(*, server_name: str):
    """
    获取指定 MCP Server 的当前工具清单；单独封装一层，便于测试时 monkeypatch。
    （猴子补丁：运行时动态替换对象 / 函数，不修改源码文件，用来 Mock 依赖，多用于单元测试。）
    """
    return await get_client().get_tools(server_name=server_name)


async def fetch_mcp_specs() -> list[ToolSpec]:
    """
    ch08 动态拉取所有 MCP Server 的工具，并转换成统一 ToolSpec。
    每台 Server 独立获取：单台不可达或超时只跳过其工具，不影响其他 Server 和本轮对话；
    工具结果格式化策略由客户端按名称挂载，最终执行仍统一交给 engine。

    main_agent
    → registry.get_all_specs()
    → mcp_client.fetch_mcp_specs()
    → 各 MCP Server get_tools()
    → 包装成 ToolSpec
    → bind_tools(...)
    """
    specs: list[ToolSpec] = []

    for server in _connections():
        try:
            # 每轮都会现拉 MCP 工具清单，因此 Server 假死会直接增加用户等待时间；
            # wait_for 在客户端侧封顶等待时长，不依赖底层 adapter 自身的超时行为。
            tools = await asyncio.wait_for(_get_tools_of(server_name=server), timeout=settings.mcp_tool_timeout)
        except Exception as e:  # noqa: BLE001
            # 单台 Server 故障按能力降级处理：本轮只少这一台提供的工具，
            # 其余工具及整个 Agent 流程继续正常运行。
            logger.warning("MCP Server「%s」不可达,本轮跳过其工具:%s", server, type(e).__name__)
            continue

        # MCP 返回的 BaseTool 统一包装为 ToolSpec；权限由 registry 本地判定，
        # formatter 也只认客户端登记，不接受 Server 自己决定结果治理方式。
        for t in tools:
            specs.append(registry.spec_from_langchain_tool(
                t, source="mcp", mcp_server=server,
                format_result=FORMATTERS.get(t.name)))

    return specs
