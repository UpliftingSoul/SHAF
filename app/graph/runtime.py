import logging
from collections.abc import AsyncIterator

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from app.config import settings
from app.core import budget, memory
from app.core import summarizer
from app.core.observability import attach_observability
from app.db import repository
from app.graph.build import build_graph

logger = logging.getLogger(__name__)

# 会流式吐答复 token 的节点(其余节点的模型调用 token 不进 delta)
ANSWER_NODES = {"main_agent"}
# 确定性节点把答复写在 state['answer'],整块作为 delta 吐出
DETERMINISTIC_ANSWER_NODES = {"script_reply", "complaint_reply", "fallback_reply"}
# 产出编号引用的检索节点(知识路 retrieve_knowledge + 退款政策路 retrieve_policy)
CITATION_NODES = {"retrieve_knowledge", "retrieve_policy"}

_graph = None
_cm = None  # AsyncSqliteSaver context manager,持有以免被 GC


class ConversationNotFound(Exception):
    pass


async def init_graph() -> None:
    """
    应用 lifespan 启动阶段初始化运行图：
    打开持久化 checkpointer、完成表结构 setup，并编译带可观测能力的 LangGraph。
    """
    global _graph, _cm

    # from_conn_string 返回异步上下文管理器；必须由模块级 _cm 持有，
    # 保证 SQLite 连接生命周期覆盖整个应用运行期，避免对象回收导致连接意外关闭。
    _cm = AsyncSqliteSaver.from_conn_string(settings.checkpointer_db_path)
    checkpointer = await _cm.__aenter__()
    await checkpointer.setup()

    # checkpointer 后续按 thread_id 持久化 State；本项目各运行入口统一用 conversation_id 作为 thread_id。
    # ch09：编译时统一挂载 Langfuse 可观测能力；未配置时原样返回图，不引入额外运行依赖。
    _graph = attach_observability(build_graph(checkpointer=checkpointer))

    logger.info("图已编译,checkpointer=%s", settings.checkpointer_db_path)


async def close_graph() -> None:
    global _graph, _cm
    if _cm is not None:
        await _cm.__aexit__(None, None, None)
        _cm = None
    _graph = None


def get_graph():
    if _graph is None:
        raise RuntimeError("图未初始化,应在 FastAPI lifespan 调 init_graph()")
    return _graph


async def get_turn_snapshot(conversation_id: int) -> dict:
    """👎 快照尽力回捞(ch09):读该会话 checkpointer 终态,返回最近一轮的用户问题与召回快照。
    调用方拿去比对被踩的 question,对得上才用快照——踩历史消息时快照可能已是别轮的,不硬塞。"""
    config = {"configurable": {"thread_id": str(conversation_id)}}
    st = await get_graph().aget_state(config)
    values = getattr(st, "values", None) or {}
    question = ""
    for m in reversed(values.get("messages", [])):
        if getattr(m, "type", "") == "human":
            question = m.content if isinstance(m.content, str) else ""
            break
    return {"question": question, "snapshot": values.get("retrieved_snapshot") or []}


def dedup_actions(actions: list) -> list:
    """跨多个 ReAct 轮次累积的 suggested_actions 可能重复(如 agent 多次提议
    create_ticket);按 type 去重,保留每种 type 首次出现的那条。"""
    seen = set()
    out = []
    for action in actions:
        t = action.get("type")
        if t in seen:
            continue
        seen.add(t)
        out.append(action)
    return out


async def _ensure_conversation(user_id: str,
                               conversation_id: int | None) -> tuple[int, str, int, int]:
    """
    返回 (cid, summary, summary_upto_msg_id, layer1_from_msg_id)：
    入口一次库读同时带出摘要及两个分层边界，供本轮上下文窗口刷新。
    """
    if conversation_id is None:
        return await repository.create_conversation(user_id), "", 0, 0

    conv = await repository.get_conversation(conversation_id)
    if conv is None:
        raise ConversationNotFound(conversation_id)

    return (conversation_id, conv.summary or "",
            conv.summary_upto_msg_id or 0, conv.layer1_from_msg_id or 0,)


def _graph_input(user_id: str, message: str, cid: int, msg_id: int,
                 summary: str, summary_upto: int,
                 layer1_from: int = 0) -> dict:
    """
    构造新一轮图输入，并初始化本轮预算与输出状态。
    配合持久化 checkpointer 显式清理易跨轮残留的字段，避免上一轮结果污染当前轮。
    """
    return {
        # messages 使用 add_messages reducer 跨轮追加，不能清空；
        # db-{msg_id} 作为数据库消息锚点，供摘要边界与分层窗口定位历史位置。
        "messages": [HumanMessage(message, id=f"db-{msg_id}")],
        "user_id": user_id,
        "conversation_id": cid,

        # 本轮执行预算每轮重新计数。
        "steps": 0,
        "tokens_used": 0,

        # 无 reducer 且并非所有分支都会覆盖的输出字段必须显式清空，
        # 否则 checkpointer 会保留旧值，例如上一轮的 answer / suggested_actions 串入本轮。
        "answer": "",
        "suggested_actions": [],
        "evidence": "",
        "evidence_strong": False,
        "citations": [],
        "evidence_confidence": 0.0,
        "fallback_source": "",
        "retrieved_snapshot": [],
        "resolved_query": "",
        "intent_confidence": 0.0,
        "order_id": "",
        "order_data": {},

        # ch07：摘要及两个分层锚点每轮从数据库读取最新值后刷新进 State。
        # summary_upto 标记摘要覆盖边界；layer1_from 标记层1原文起点，其前历史按层2形态渲染。
        "summary": summary,
        "summary_upto_msg_id": summary_upto,
        "layer1_from_msg_id": layer1_from,

        # trace 使用 merge_dict 通道；None 是重置哨兵，用于清除上一轮 trace 残留。
        "trace": None,
    }


def _interrupt_payload(state: dict):
    """
    从终态提取中断负载；无中断时返回 None。
    ch08 起中断不止订单选择器，因此透传完整的 __interrupt__[0].value，
    由消费方根据 type 分发，保留 orders、preview 等不同类型的数据。
    """
    intr = state.get("__interrupt__")
    if not intr:
        return None
    return intr[0].value


async def _final_state(cid: int) -> dict:
    """
    从 LangGraph checkpointer 读取指定会话的最新 State。
    流式 astream 不直接返回完整终态，轮末分层结算需要通过 thread_id 重新获取。
    找不到快照时返回空字典。
    """
    snap = await get_graph().aget_state({"configurable": {"thread_id": str(cid)}})
    return snap.values if snap else {}


async def settle_layers(cid: int, state: dict | None,
                        summary_upto: int, layer1_from: int) -> None:
    """
    ch07 轮末分层结算：检查 Layer 1 是否超出预算，必要时整批降级到 Layer 2。
    summary_upto、layer1_from 是本轮入口读取的两个边界；
    state 为 None 时从 checkpointer 读取终态，供流式路径使用。

    降级只在超预算时发生，并一次性推进到目标边界，避免每轮微调导致
    半压历史不断变化、破坏模型请求的稳定前缀。异常只记录日志，不影响当轮回复。
    """
    try:
        # 非流式路径直接使用 ainvoke 的终态；流式路径需自行读取最新 State。
        if state is None:
            state = await _final_state(cid)

        msgs = (state or {}).get("messages") or []
        if not msgs:
            return

        # Layer 1 使用总滑窗预算中 layer1_ratio 对应的份额。
        # layer_tokens 根据当前双锚点计算实际用量，未超预算则保持原边界不动。
        l1_budget = int(budget.compute().sliding * settings.layer1_ratio)
        _, layer1_tok = memory.layer_tokens(msgs, summary_upto, layer1_from)
        if layer1_tok <= l1_budget:
            return

        # 超预算时，从当前历史倒推新的 Layer 1 起点，一次性降级足够多的旧轮次。
        # 只有新边界确实向前推进，才持久化更新，避免无意义的边界写入。
        new_from = memory.next_layer1_from(msgs, summary_upto, l1_budget)
        if new_from > layer1_from:
            await repository.update_layer1_from(cid, new_from)
            logger.info("层1 降级 conv=%s %s→%s(层1 %s > 预算 %s)",
                        cid, layer1_from, new_from, layer1_tok, l1_budget)

    except Exception:
        # 分层结算属于轮末维护工作，失败不能影响已经生成的正常回复。
        logger.exception("分层结算失败 conv=%s(不影响本轮回复)", cid)


async def run_turn(user_id, message, conversation_id) -> dict:
    """
    新一轮非流式入口，供 /api/agent 与评估脚本使用。
    校验会话并落库用户消息后，通过 ainvoke 执行至终态或下一处 interrupt；
    轮末依次结算 Layer 1、检查 Layer 2 摘要触发，最后返回 State 与中断信息。
    """
    # 统一校验会话并读取当前摘要及双锚点；用户消息先落库，再交给 Agent 处理。
    cid, summary, upto, layer1 = await _ensure_conversation(user_id, conversation_id)
    msg_id = await repository.append_message(cid, "user", content=message)

    # conversation_id 同时作为 LangGraph thread_id，续接该会话持久化的 State；
    # ch09：langfuse_session_id 将同一会话的多轮 trace 归为一组。
    config = {"configurable": {"thread_id": str(cid)},
              "metadata": {"langfuse_session_id": str(cid)}}

    # 获取当前State
    final = await get_graph().ainvoke(
        _graph_input(user_id, message, cid, msg_id, summary, upto, layer1), config)

    # ch07 轮末先结算 Layer 1：超预算才整批降级，并将新起点写入数据库。
    # 再检查 Layer 2：降级后若超出自身预算，才启动后台分段摘要。
    # 本轮仍使用入口读取的旧边界；新的边界与摘要由后续轮次重新读取。
    await settle_layers(cid, final, upto, layer1)
    await summarizer.maybe_schedule_summary(cid)

    return {"conversation_id": cid, "state": final, "interrupt": _interrupt_payload(final)}


async def resume_turn(conversation_id: int, resume_value) -> dict:
    """
    非流式中断续跑入口，供 /api/agent、评估脚本等调用。
    将 resume_value 通过 Command(resume=...) 回填给暂停处的 interrupt()，
    沿用同一 conversation_id 对应的 checkpoint，继续执行到下一次中断或图结束。
    """
    # 续跑没有新用户消息，也不经过 _ensure_conversation；
    # 因此先显式确认目标会话存在，再恢复其持久化执行现场。
    if await repository.get_conversation(conversation_id) is None:
        raise ConversationNotFound(conversation_id)

    # 必须沿用原 conversation_id 作为 thread_id，LangGraph 才能找到此前暂停的 checkpoint。
    config = {"configurable": {"thread_id": str(conversation_id)},
              "metadata": {"langfuse_session_id": str(conversation_id)}}

    # resume_value 原样成为 interrupt() 的返回值：
    # ch06 订单选择为订单号字符串；ch08 工单确认则为 {"confirmed": bool}。
    final = await get_graph().ainvoke(Command(resume=resume_value), config)

    await summarizer.maybe_schedule_summary(conversation_id)

    return {"conversation_id": conversation_id, "state": final,
            "interrupt": _interrupt_payload(final)}


async def _stream_events(cid: int, stream_source) -> AsyncIterator[dict]:
    """
    将 LangGraph astream 的 messages / updates 两路输出转换为前端事件 dict。
    stream_source 可以是新一轮的 _graph_input(...)，也可以是续跑用的 Command(resume=...)；
    遇到 interrupt 时立即发送中断事件并结束本次流，等待前端操作后另行续跑。
    """
    config = {"configurable": {"thread_id": str(cid)},
              "metadata": {"langfuse_session_id": str(cid)}}
    actions: list = []

    # messages 提供模型流式输出，updates 提供各节点的状态更新；
    # 两路同时监听，分别提取答案 token、引用、工具调用、中断等前端事件。
    async for mode, chunk in get_graph().astream(
            stream_source, config, stream_mode=["messages", "updates"],
    ):
        if mode == "messages":
            msg, meta = chunk

            # 图内多个节点都会调用模型，但只有 ANSWER_NODES 的 token 才属于用户最终答复；
            # 意图分类、Query 改写、证据自评等内部模型输出必须过滤，不能混入前端 delta。
            if meta.get("langgraph_node") in ANSWER_NODES:
                text = msg.content if isinstance(msg.content, str) else ""
                if text:
                    yield {"type": "delta", "text": text}

        elif mode == "updates":
            # LangGraph interrupt 出现在 updates 中；本次流在发送 interrupt 后立即结束，
            # 前端根据 conversation_id 和业务负载完成选择，再通过续跑接口恢复同一线程。
            if "__interrupt__" in chunk:
                payload = chunk["__interrupt__"][0].value

                # interrupt 帧发出后会立即 return，本次 runtime 流不会再走到末尾的 done；
                # 正常结束时 conversation_id 本可从 done 获取，因此中断场景必须在这里单独携带，前端拿着它才知道续跑要用哪个会话。
                # ch08：不同 kind 透传各自业务负载，如 select_order→orders、confirm_ticket→preview。
                ev = {"type": "interrupt",
                      "kind": payload.get("type", ""),
                      "conversation_id": cid}
                for k in ("orders", "preview"):
                    if payload.get(k) is not None:
                        ev[k] = payload[k]

                yield ev
                return

            for node, upd in chunk.items():
                if not isinstance(upd, dict):
                    continue

                # 确定性答复节点不调用模型，没有逐 token 输出；
                # 直接把 state['answer'] 整块包装成 delta，前端无需区分答复来源。
                if node in DETERMINISTIC_ANSWER_NODES and upd.get("answer"):
                    yield {"type": "delta", "text": upd["answer"]}

                # 引用由检索节点直接产出，与答案文本分成独立 citations 帧发送。
                if node in CITATION_NODES and upd.get("citations"):
                    yield {"type": "citations", "items": upd["citations"]}

                # 工具执行结果从 agent_tools 的消息中提取工具名，供前端展示调用轨迹。
                if node == "agent_tools":
                    for m in upd.get("messages", []):
                        name = getattr(m, "name", None)

                        # submit_refund 仍是“拦成前端表单”的合成消息，不属于真实工具执行；
                        # create_ticket 自 ch08 起确认后会实际执行，因此正常发送 tool 帧。
                        if name and name != "submit_refund":
                            yield {"type": "tool", "name": name}

                # ReAct 可能多轮产生相同建议动作，先跨节点/轮次累积，流结束时统一去重发送。
                if upd.get("suggested_actions"):
                    actions.extend(upd["suggested_actions"])

    if actions:
        yield {"type": "actions", "items": dedup_actions(actions)}

    # 仅正常跑完图时发送 done；interrupt 分支已在上方提前结束。
    yield {"type": "done", "conversation_id": cid}


async def stream_turn(user_id, message, conversation_id) -> AsyncIterator[dict]:
    """
    新一轮流式入口，供 /api/chat 使用。
    用户消息落库后持续产出事件 dict，由接口层转换为 SSE；
    事件流结束后依次结算 Layer 1、检查 Layer 2 摘要触发，不等待后台摘要完成。
    """
    cid, summary, upto, layer1 = await _ensure_conversation(user_id, conversation_id)
    msg_id = await repository.append_message(cid, "user", content=message)

    async for ev in _stream_events(
            cid, _graph_input(user_id, message, cid, msg_id, summary, upto, layer1)):
        yield ev

    # ch07：流式路径没有直接返回的完整终态，settle_layers 内部从 checkpointer 读取。
    # 先降级 Layer 1，再检查 Layer 2；后台摘要不阻塞已经完成的事件流。
    await settle_layers(cid, None, upto, layer1)
    await summarizer.maybe_schedule_summary(cid)


async def stream_resume(conversation_id: int, resume_value) -> AsyncIterator[dict]:
    """
    流式中断续跑入口：用 Command(resume=...) 恢复指定会话的暂停执行，
    并继续通过 _stream_events 产出与 stream_turn 同构的事件流。
    因此 /api/actions/resume 与 /api/chat 可以复用同一套前端事件解析逻辑。
    """
    # 与非流式续跑一致：这里不经过 _ensure_conversation，需自行校验目标会话。
    if await repository.get_conversation(conversation_id) is None:
        raise ConversationNotFound(conversation_id)

    # _stream_events 内部仍以 conversation_id 作为 thread_id；
    # resume_value 不在 runtime 解读，直接交给暂停节点的 interrupt() 接收。
    async for ev in _stream_events(
            conversation_id, Command(resume=resume_value)):
        yield ev

    await summarizer.maybe_schedule_summary(conversation_id)
