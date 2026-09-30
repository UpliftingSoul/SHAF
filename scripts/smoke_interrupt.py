"""
ch06 interrupt / Command(resume) 红线冒烟脚本。

本脚本不调用任何聊天模型，只构造一个“选择订单 → 确认订单”的最小 LangGraph，
用于提前验证后续退款子流程与 runtime 依赖的四条中断行为：

A) 非流式首次执行：
   ainvoke 遇到 interrupt 后暂停，返回态包含 __interrupt__；
   中断业务负载从 out["__interrupt__"][0].value 取得。

B) 非流式续跑：
   使用相同 thread_id 调用 Command(resume=v) 后，
   resume 的 v 会成为原 interrupt(...) 的返回值，节点从暂停位置继续执行。

C) 流式执行：
   stream_mode=["messages", "updates"] 时，中断出现在 updates 路；
   runtime 因此应从 updates 的 __interrupt__ 中提取并发送 interrupt 事件。
   流结束后也可通过 aget_state 查看当前线程仍处于哪个 pending 节点。

D) 图外直接调用中断节点：
   interrupt() 依赖 LangGraph 的运行上下文，本章环境下直接调用节点会触发 RuntimeError，
   因此包含 interrupt 的路径必须通过【编译后的图】执行测试，不能直接 await 节点函数。

这些结论会直接约束后续 fetch_order 的恢复逻辑、runtime 的 SSE 中断处理以及相关测试写法。
运行方式：.venv/bin/python -m scripts.smoke_interrupt
"""

import asyncio
from typing import Annotated

from typing_extensions import TypedDict

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Command, interrupt


class S(TypedDict):
    """冒烟图最小 State：保留消息历史，并记录用户最终选择的订单。"""

    messages: Annotated[list, add_messages]
    picked: str


async def ask_order(state: S):
    """
    纯中断节点：抛出选择订单请求并暂停图执行。
    后续 Command(resume=v) 恢复时，v 会作为 interrupt() 的返回值赋给 picked。
    """
    picked = interrupt({
        "type": "select_order",
        "orders": [{"order_id": "1001"}, {"order_id": "2002"}],
    })
    return {"picked": picked}


async def confirm(state: S):
    """读取恢复后写入 State 的 picked，生成一条订单确认消息。"""
    return {"messages": [("ai", f"已选订单 {state['picked']}")]}


async def main():
    """构造最小持久化图，依次验证非流式、流式、续跑和图外调用四类中断行为。"""

    # resume 必须依赖 checkpoint 恢复暂停现场；
    # 冒烟测试只需覆盖单进程生命周期，因此直接使用内存 SQLite。
    async with AsyncSqliteSaver.from_conn_string(":memory:") as cp:
        await cp.setup()

        b = StateGraph(S)
        b.add_node("ask_order", ask_order)
        b.add_node("confirm", confirm)
        b.add_edge(START, "ask_order")
        b.add_edge("ask_order", "confirm")
        b.add_edge("confirm", END)
        graph = b.compile(checkpointer=cp)

        # -------------------------- A) 非流式首次执行：观察中断返回态 --------------------------
        # interrupt 会暂停图；业务负载位于 __interrupt__ 列表中第一个对象的 value。
        config = {"configurable": {"thread_id": "smoke-int-1"}}
        out = await graph.ainvoke(
            {"messages": [("human", "我要退款")]},
            config,
        )
        print("A ainvoke keys=", list(out.keys()))
        print("A __interrupt__=", out.get("__interrupt__"))
        if out.get("__interrupt__"):
            print("A first.value=", out["__interrupt__"][0].value)

        # -------------------------- B) 非流式续跑：验证 resume 值回填 --------------------------
        # 必须沿用同一 thread_id 才能找到刚才的 checkpoint；
        # "1001" 会成为 ask_order 中 interrupt(...) 的返回值，并写入 picked。
        out2 = await graph.ainvoke(Command(resume="1001"), config)
        print(
            "B resume picked=", out2.get("picked"),
            "msgs=", [m.content for m in out2["messages"]],
        )

        # -------------------------- C) 流式首次执行：确认中断出现在哪一路 --------------------------
        config2 = {"configurable": {"thread_id": "smoke-int-2"}}
        print("C astream chunks:")

        # messages 路负责模型消息流，updates 路负责节点状态更新；
        # 本冒烟用于确认 interrupt 由 updates 的 __interrupt__ 暴露给 runtime。
        async for mode, chunk in graph.astream(
            {"messages": [("human", "我要退款")]},
            config2,
            stream_mode=["messages", "updates"],
        ):
            if mode == "updates":
                print("  UPD keys=", list(chunk.keys()), "val=", chunk)

        # C') 中断后也可读取 checkpoint 快照，观察 pending 节点和 interrupt 信息。
        # 这是流事件之外的备用状态探测方式，不是 runtime 主路径。
        snap = await graph.aget_state(config2)
        print(
            "C' aget_state .next=", snap.next,
            "interrupts=", getattr(snap, "interrupts", None),
            "tasks_interrupts=", [t.interrupts for t in snap.tasks],
        )

        # C'') 流式续跑同样使用 Command(resume) + 原 thread_id；
        # 恢复后继续收到正常 messages / updates 事件，直到图结束。
        print("C'' astream resume:")
        async for mode, chunk in graph.astream(
            Command(resume="2002"),
            config2,
            stream_mode=["messages", "updates"],
        ):
            print(
                "  ",
                mode,
                chunk if mode == "updates"
                else (chunk[0].content, chunk[1].get("langgraph_node")),
            )

        # -------------------------- D) 图外直接调用中断节点 --------------------------
        # interrupt() 需要 LangGraph runnable 上下文；本章环境下直接 await 节点会报 RuntimeError。
        # 因此 fetch_order 等节点的 interrupt 分支测试必须经编译图触发，不能直接调用节点函数。
        from langgraph.errors import GraphInterrupt

        try:
            await ask_order({"messages": []})
            print("D no error(unexpected)")
        except GraphInterrupt as e:
            # 保留该分支用于观察不同 LangGraph 版本/执行环境下可能暴露的内部中断异常。
            print(
                "D GraphInterrupt first.value=",
                e.args[0][0].value if e.args else None,
            )
        except RuntimeError as e:
            print("D RuntimeError(图外不能调 interrupt):", e)
            print(
                "D 结论:fetch_order 的缺单 interrupt 路径须经【编译图 ainvoke】测,"
                "不能直接调节点函数。"
            )


if __name__ == "__main__":
    asyncio.run(main())

