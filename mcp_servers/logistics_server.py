"""
ch08 · 物流 MCP Server。

独立进程提供 query_logistics 物流查询工具；本章使用稳定的 mock 数据，
不连接真实物流系统、不建业务表。客服主进程通过 MCP 动态发现并调用该工具。

运行：uv run python mcp_servers/logistics_server.py
可设置 MOCK_DELAY_SECONDS 人为增加响应延迟，用于验收客户端的超时、重试与审计机制。
当前基于 mcp==1.28.1，使用 streamable-http 传输，默认 MCP 路径为 /mcp。
"""

import asyncio
import os
import random
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field

# FastMCP实例，服务标识logistics；端口优先读环境变量PORT，默认8101，仅本机监听
mcp = FastMCP("logistics", host="127.0.0.1", port=int(os.environ.get("PORT", "8101")))

_DELAY = float(os.environ.get("MOCK_DELAY_SECONDS", "0"))
_STATUS_CODES = ["PICKED_UP", "IN_TRANSIT", "DELIVERING", "DELIVERED"]
_CITIES = ["深圳", "广州", "杭州", "上海", "成都"]


@mcp.tool()
async def query_logistics(
    tracking_no: Annotated[str, Field(description="物流单号(形如 SF 开头),需先用 query_order 查订单拿到该单号")],
) -> dict:
    """
    用物流单号查询当前物流状态、所在城市和运输轨迹。
    物流单号不同于订单号；若用户只提供订单号，应先通过 query_order 获取 tracking_no。
    """
    # 测试环境可注入固定延迟，模拟 MCP Server 响应过慢或超时。
    if _DELAY > 0:
        await asyncio.sleep(_DELAY)

    # 使用物流单号作为随机种子，使同一单号每次查询得到稳定的 mock 结果。
    rng = random.Random(f"logistics:{tracking_no}")
    code = rng.choice(_STATUS_CODES)
    city = rng.choice(_CITIES)

    # 这里最重要的是保留了一个边界：Server 返回自己的原始业务结构，不负责替客服系统“翻人话”。
    # status_code、carrier_code 都可以正常存在；哪些字段最终给模型、怎么翻译，由使用方治理。
    return {
        "tracking_no": tracking_no,
        "status_code": code,          # Server 保留内部枚举码，由客服侧 client 统一翻译
        "current_city": city,
        "trace": [f"{city}分拨中心 已发出", f"内部状态码:{code}"],
        "carrier_code": "SF-EXP-01",  # 内部字段，客服侧格式化时会剔除
    }


if __name__ == "__main__":
    # # 启动MCP服务，使用streamable‑http传输协议（支持HTTP长流，兼容标准MCP客户端）
    mcp.run(transport="streamable-http")
