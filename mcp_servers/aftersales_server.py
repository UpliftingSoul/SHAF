"""
ch08 · 售后 MCP Server。

独立进程提供两个售后查询工具：
query_warranty 查询订单商品保修状态，query_return_status 查询退货进度。
本章同样使用稳定 mock 数据，不连接真实售后系统、不新增业务表。

运行：uv run python mcp_servers/aftersales_server.py
可设置 MOCK_DELAY_SECONDS 注入响应延迟，用于验收客户端超时与故障降级。
"""

import asyncio
import os
import random
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field


# 容器间访问需要监听所有容器接口；默认仍兼容本地开发。
mcp = FastMCP("aftersales", host=os.environ.get("HOST", "127.0.0.1"),
              port=int(os.environ.get("PORT", "8102")))

_DELAY = float(os.environ.get("MOCK_DELAY_SECONDS", "0"))


@mcp.tool()
async def query_warranty(
    order_id: Annotated[str, Field(description="订单号,例如 1001")],
) -> dict:
    """查询订单商品的保修状态和保修到期日。用于用户询问是否在保、保修期限等问题。"""
    if _DELAY > 0:
        await asyncio.sleep(_DELAY)

    # 订单号作为随机种子，保证同一订单的 mock 查询结果稳定。
    rng = random.Random(f"warranty:{order_id}")
    code = rng.choice(["IN_WARRANTY", "EXPIRED"])

    return {
        "order_id": order_id,
        "warranty_code": code,  # 内部状态码，由客服侧 client 转成“在保 / 已过保”
        "warranty_until": f"2026-{rng.randint(8, 12):02d}-{rng.randint(1, 28):02d}",
        "policy_ref": "AS-POLICY-07",  # 内部政策编号，客服回答无需使用
    }


@mcp.tool()
async def query_return_status(
    order_id: Annotated[str, Field(description="订单号,例如 1001")],
) -> dict:
    """查询订单当前退货进度。用于用户询问退货审核、退回或退款进行到哪一步。"""
    if _DELAY > 0:
        await asyncio.sleep(_DELAY)

    rng = random.Random(f"return:{order_id}")
    code = rng.choice(["AUDITING", "RETURNING", "REFUNDED", "NONE"])

    return {
        "order_id": order_id,
        "return_code": code,  # 内部状态码，由客服侧 client 转成面向用户的状态文本
        "updated_at": f"2026-07-{rng.randint(1, 16):02d} 10:00",
    }


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
