import json
import logging
import re

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.types import interrupt

from app.config import settings
from app.core import coref
from app.core import intent as intent_mod
from app.core import memory
from app.core import query_understanding, retrieval, selfcheck
from app.core.confidence import compute_evidence_confidence, snapshot_from_hits
from app.core.llm import get_chat_model
from app.core.observability import tag_intent
from app.core.prompts import (
    AGENT_SYSTEM, COMPLAINT_REPLY_TEXT, FALLBACK_REPLY_TEXT,
    REFUND_JUDGE_HINT, SCRIPT_REPLY_CHITCHAT, SCRIPT_REPLY_OTHER,
)
from app.db import repository
from app.graph.routing import INTENT_TO_ROUTE
from app.tools import business, engine, registry

logger = logging.getLogger(__name__)

COMPLAINT_REPLY = COMPLAINT_REPLY_TEXT
FALLBACK_REPLY = FALLBACK_REPLY_TEXT


def _user_text(state) -> str:
    for m in reversed(state.get("messages", [])):
        if isinstance(m, HumanMessage):
            return m.content or ""
    return ""


def _history_text(state, max_turns: int = 6) -> str:
    """
    为 coref / 意图识别构造共享历史文本：早期摘要 + 分层滑窗中的近期消息。
    先按摘要、Layer 1 双锚点构造当前滑窗，再取最近若干条历史；
    已退出滑窗的更早信息由摘要补足。
    """
    msgs = memory.build_window(state.get("messages", []),
                               state.get("summary_upto_msg_id") or 0,
                               state.get("layer1_from_msg_id") or 0)

    # 最后一条 HumanMessage 是本轮正在被消解 / 分类的问题，query 已单独传入；
    # 再放进 history 会让同一句同时出现在 history 和 query 中，干扰判断。
    prior = msgs[:-1] if msgs else []

    # 构建供指代消解和意图识别环节使用的上下文
    # （注：跨太多轮指代本身少见，由此设置较大的max_turns将不必要地增大token开销与模型理解难度）
    lines = []
    for m in prior[-max_turns:]:
        role = "用户" if isinstance(m, HumanMessage) else "客服"
        text = m.content if isinstance(m.content, str) else ""
        if text:
            lines.append(f"{role}:{text}")
    body = "\n".join(lines)

    # 摘要补足已经退出当前滑窗的远端事实与指代线索。
    head = memory.summary_line(state.get("summary"))
    return f"{head}\n{body}".strip() if head else body


# 4+ 位连续数字视作订单号;用 lookaround 而非 \b——CJK 与数字同属 \w,\b 在「订单1001」处不成立
_ORDER_RE = re.compile(r"(?<!\d)(\d{4,})(?!\d)")


def _extract_order_id(text: str) -> str | None:
    m = _ORDER_RE.search(text or "")
    return m.group(1) if m else None


async def fetch_order(state) -> dict:
    """
    退款子流程第一步：确定并校验目标订单，再读取订单快照。
    优先使用 State 中已有订单号，否则从消解后的问题中抽取；
    缺失或不属于当前用户时，通过 interrupt 弹出订单选择器，由前端点选并通过 resume 回填。

    归属校验必须在本节点完成：refund_flow 的这一步是确定性流程，不经过 agent_tools，
    因此用户输入和 resume 回传的订单号都不能依赖工具层校验。
    """
    uid = state.get("user_id", "")
    oid = state.get("order_id") or _extract_order_id(
        state.get("resolved_query") or _user_text(state))

    # 缺订单号和“订单不属于当前用户”统一进入选择流程。
    # 使用 while 而非 if：resume 回填值同样属于外部输入，恢复后仍必须重新做归属校验。
    while not business.owns_order(uid, str(oid or "")):
        # interrupt 恢复时节点会从头重新执行，因此中断前只允许抽取、查询等只读操作，
        # 避免写库、扣款等副作用因重跑而重复发生。
        orders = business.list_user_orders(uid)
        oid = interrupt({"type": "select_order", "orders": orders})

    data = business.order_snapshot(oid)

    return {"order_id": oid, "order_data": data,
            "trace": {"fetch_order": {"order_id": oid}}}


async def retrieve_policy(state) -> dict:
    """
    退款子流程的强制政策检索节点：将当前问题扩写为最多 3 条独立查询，多路检索后
    按 chunk id 去重并保留最高相关分，再整理为编号证据与 citations 注入 main_agent。
    README L194：退款判断必须基于检索证据，不允许模型仅凭自身记忆回答。
    """
    base = state.get("resolved_query") or _user_text(state)
    od = state.get("order_data") or {}

    # 同一句“能退吗”在[未发货]、[已签收]等状态下适用政策不同；
    # 将订单状态加入检索种子，提高召回条款与当前订单实际阶段的匹配度。
    seed = f"{base} {od.get('status', '')}".strip()

    # 退款政策可能散落在退货条件、时效、运费等不同知识块，
    # 扩写为多条独立 query 分别检索，比单 query 更容易覆盖完整政策证据。
    queries = await query_understanding.expand_queries(seed)

    # 同一 chunk 可能被多条 query 重复召回；按 id 合并并保留其最高 rerank_score，
    # 避免重复证据，同时用该 chunk 最佳的一次命中表现参与最终排序。
    merged: dict = {}
    for q in queries:
        for h in await retrieval.search_knowledge(
                q, strategy="hybrid_rerank", bm25_text=q):
            cur = merged.get(h["id"])
            if cur is None or h["rerank_score"] > cur["rerank_score"]:
                merged[h["id"]] = h

    ranked = sorted(
        merged.values(),
        key=lambda h: h["rerank_score"],
        reverse=True,
    )

    # 调整文档顺序缓解 lost-in-middle；该顺序同时作为后续引用编号顺序。
    arranged = retrieval.arrange_head_tail(ranked)
    citations = [
        {"n": i + 1, "id": h["id"], "section_path": h["section_path"],
         "question": h["question"], "answer": h["answer"],
         "content_type": h["content_type"]}
        for i, h in enumerate(arranged)
    ]

    # evidence 直接供 main_agent 判断退款资格；citations 保留结构化引用信息供前端使用。
    evidence = "\n".join(
        f"[{c['n']}] {c['question']}: {c['answer']}"
        for c in citations
    )

    return {"evidence": evidence, "citations": citations,
            "trace": {"retrieve_policy": {
                "queries": queries,
                "hits": len(ranked),
            }}}


async def script_reply(state) -> dict:
    """fallback_script 出口:闲聊/其他 兜底话术(零模型),按 intent 分文案——闲聊把话题引回产品,
    其他请用户说具体些。分流后立即命中、不进 Agent。
    (与 ch05 知识路证据弱的 fallback_reply 是两码事,名字相近但不同节点、不同触发点,勿混。)"""
    text = SCRIPT_REPLY_OTHER if state.get("intent") == "其他" else SCRIPT_REPLY_CHITCHAT
    return {"answer": text, "trace": {"route": "fallback_script"}}


async def complaint_reply(state) -> dict:
    """
    【投诉】零模型出口：直接返回固定安抚话术，不让模型临场生成。
    同时提供转人工、创建工单两个 suggested_actions，仅交前端展示选择，后端不直接执行；
    工单 draft 预填当前投诉描述与类型，用户确认后无需重复采集。
    """
    actions = [
        {"type": "transfer_human"},
        {"type": "create_ticket",
         "draft": {"description": _user_text(state), "ticket_type": "投诉"}},
    ]

    return {"answer": COMPLAINT_REPLY, "suggested_actions": actions,
            "trace": {"route": "complaint"}}


async def fallback_reply(state) -> dict:
    """
    低置信兜底出口：返回固定兜底话术，并将当前问题写入低置信池供数据飞轮处理。
    source 区分 retrieval_low_conf / self_check 两道检索校验入口；
    同时保存判定依据与当轮召回快照，供后续人工审核复盘。
    """
    source = state.get("fallback_source") or "retrieval_low_conf"

    # reason 留下综合置信度及四个原始信号；
    # 若由 self_check 拦截，再附上模型的证据不足理由，便于审核时还原“为什么这次没答”。
    signals = state.get("trace", {}).get("confidence_signals") or {}
    reason = (f"evidence_confidence={state.get('evidence_confidence', 0.0):.3f} "
              f"signals={json.dumps(signals, ensure_ascii=False)}")
    if source == "self_check":
        reason += f" self_check={state.get('trace', {}).get('self_check', '')}"

    # 当前两种 fallback_source 都由 retrieve_knowledge 的校验闸产生，
    # 因此可据此判断本轮是否确实执行过检索。
    walked_retrieval = bool(state.get("fallback_source"))

    # 召回快照严格保留三态：
    # 有内容=list；已检索但零命中=[]；未走检索=None。
    # ❗ [] 是“真缺知识”的强信号，不能与 None 混淆。
    snapshot = state.get("retrieved_snapshot") or ([] if walked_retrieval else None)

    await repository.insert_low_confidence(
        state.get("conversation_id"), _user_text(state), source, reason,
        retrieved_chunks=snapshot,
    )

    # 兜底话术既然引导用户联系人工，就同步给前端可执行的转人工动作，
    # 避免只有文字提示却没有对应入口。
    return {"answer": FALLBACK_REPLY,
            "suggested_actions": [{"type": "transfer_human"}],
            "trace": {"route": "fallback"}}


async def resolve_reference(state) -> dict:
    """指代消解 + Query 改写(合一):吃最近几轮历史把半截话补成完整问句;已完整则透传。
    写 resolved_query,供 classify_intent 与 refund_flow 检索共用(意图识别不再改写)。"""
    query = _user_text(state)
    history = _history_text(state)
    # ch07 验收可观测:每轮必经此节点,摘要行+滑窗在这一条日志里直接可见
    # (model_ctx 只在进 main_agent 的路由才有,闲聊/兜底路由靠这条看)
    logger.info("history_ctx conv=%s(摘要行+滑窗,coref/意图共用)\n%s",
                state.get("conversation_id"), history or "(无历史)")
    resolved = await coref.resolve(query, history)
    mode = "rewrite" if resolved != query else "passthrough"
    return {"resolved_query": resolved, "trace": {"coref": mode}}


async def classify_intent(state) -> dict:
    """
    意图分类节点：基于 resolved_query（为空则回落用户原话）+ 最近历史，判定八类意图及置信度。
    历史上下文用于应对物流→退款→物流等意图漂移；「其他」作为兜底类别。
    同时按 INTENT_TO_ROUTE 计算 route 并落入 State，供后续上下文注入、日志留痕等节点复用；
    条件边也使用同一映射表分流，保证路由规则单一来源、不漂移。
    """
    query = state.get("resolved_query") or _user_text(state)
    r = await intent_mod.classify(query, _history_text(state))
    intent, conf = r["intent"], r["confidence"]
    route = INTENT_TO_ROUTE.get(intent, "business")

    # ch09：将当前意图写入 trace metadata/tag，供 Cost Control 按意图分组统计
    tag_intent(intent, conf)

    return {"intent": intent, "intent_confidence": conf, "route": route,
            "trace": {"intent": intent, "intent_confidence": conf, "route": route}}


async def retrieve_knowledge(state) -> dict:
    """知识通路强制检索节点，底层逻辑与query_faq工具函数同源对齐。
    强制检索：不给模型自主选择是否检索的权利，规避模型幻觉带来政策类高代价错误。
    ch09将原有简单机械闸升级为四信号加权正式置信度闸，闸位置不变：检索完成后、送入Agent之前。
    trace携带forced_rag=True作为本节点执行标记；只要执行检索就保存Top3召回快照，
    校验不通过用于问题落池写库；校验通过存入state，供后续用户负反馈👎回捞排查。
    """
    query_raw = _user_text(state)
    u = await query_understanding.understand(query_raw)
    query = u["standard"]
    # 同义词扩展只拼进 BM25 检索文本(检索侧),不改 dense query、不改标准问法语义(ch04 spec §2)
    # * BM25 是词面匹配，多给几个近义词纯赚，用户说「邮费」而文档写「运费」，扩展词正好把这个缺口补上。
    # * 但向量检索不一样，它编码的是整句的语义，在标准问法后面缀一串同义词，句子的语义重心会被这串词带偏，检索结果反而变差。
    bm25_text = query + (" " + " ".join(u["expanded"]) if u["expanded"] else "")

    hits = await retrieval.search_knowledge(
        query, strategy="hybrid_rerank", bm25_text=bm25_text)

    # ch09新增：四信号加权计算综合证据置信度
    conf = compute_evidence_confidence(hits)
    base = {"evidence_confidence": conf.score,
            "retrieved_snapshot": snapshot_from_hits(hits)}

    # -------------------------- 两道证据校验闸（生成回答之前执行，节约LLM生成开销） --------------------------
    # 置信度闸(ch09升级替代原机械闸)：加权综合分数低于校准阈值 → 证据弱，拒答落池
    if conf.score < settings.evidence_confidence_threshold:
        return {**base, "evidence_strong": False, "fallback_source": "retrieval_low_conf",
                "trace": {"forced_rag": True, "evidence_confidence": conf.score,
                          "confidence_signals": conf.signals}}

    # 语义闸：LLM二次自评证据是否足以回答用户问题
    # 应对风险点：检索高分 ≠ 证据可用，例如退货运费文本相似度高，但无法回答换货运费责任问题
    # 要求自评模型严格依据召回证据，禁止使用自身常识脑补补充信息
    ev_texts = [f"{h['question']} {h['answer']}" for h in hits]
    chk = await selfcheck.check_sufficient(query, ev_texts)
    if not chk["useful"]:
        return {**base, "evidence_strong": False, "fallback_source": "self_check",
                "trace": {"forced_rag": True, "evidence_confidence": conf.score,
                          "confidence_signals": conf.signals, "self_check": chk["reason"]}}

    # -------------------------- 结果格式化输出 --------------------------
    # 调整文档顺序，缓解大模型lost‑in‑middle问题；该顺序同时作为引用编号顺序
    arranged = retrieval.arrange_head_tail(hits)
    # citations：结构化元数据，供给前端展示跳转，n引用标号与送入大模型的上下文顺序严格对齐
    citations = [
        {"n": i + 1, "id": h["id"], "section_path": h["section_path"],
         "question": h["question"], "answer": h["answer"], "content_type": h["content_type"]}
        for i, h in enumerate(arranged)
    ]
    # evidence：拼接好的纯文本，直接喂给大模型用于生成答案
    evidence = "\n".join(f"[{c['n']}] {c['question']}: {c['answer']}" for c in citations)

    return {**base, "evidence_strong": True, "evidence": evidence, "citations": citations,
            "trace": {"forced_rag": True, "evidence_confidence": conf.score,
                      "confidence_signals": conf.signals}}


async def confidence_check(state) -> dict:
    """置信度兜底(生成前证据闸)的实体节点:留痕门控决策供可观测;
    强/弱的实际分流在其后的条件边 confidence_gate(读 evidence_strong)。"""
    decision = "strong" if state.get("evidence_strong") else "weak"
    return {"trace": {"confidence": decision}}


_KNOWLEDGE_EVIDENCE_HINT = (
    "\n\n## 已检索到的知识证据(请据此作答,每个关键结论后标注来源编号如[1];"
    "证据已给,不要再调用 query_faq;仍可按需调用订单/物流等工具)\n"
    "型号编号逐字复制证据里的写法,证据里没有的型号不要写;带条件的结论要连条件一起说。\n"
)


def _turn_context(state) -> str:
    """
    拼装主 Agent 当前调用所需的动态上下文：早期摘要、检索证据和退款订单数据。
    这些可变内容不进入固定 system，后续统一作为临时 HumanMessage 注入；
    全部为空时返回空串。
    """
    parts = []

    # 摘要描述更早发生的对话历史，因此始终排在本轮业务材料之前。
    ss = memory.summary_system(state.get("summary"))
    if ss is not None:
        parts.append("\n\n" + ss.content)

    # 知识路已经强制检索过一次；hint 明确阻止重复调用 query_faq，
    # 但仍允许 Agent 按需调用订单、物流等其他工具。
    if state.get("evidence"):
        parts.append(_KNOWLEDGE_EVIDENCE_HINT + state["evidence"])

    # 顺序不能反：REFUND_JUDGE_HINT 的措辞默认前文已经给出检索证据，
    # 因此退款流程的订单数据必须排在 evidence 之后。
    if state.get("route") == "refund_flow":
        parts.append(REFUND_JUDGE_HINT + json.dumps(state.get("order_data", {}), ensure_ascii=False))

    # 本章只调整动态材料的消息承载位置，不同步改提示词措辞，
    # 避免缓存效果变化时无法区分是消息结构还是提示词变化导致。
    return "".join(parts)


# ch07-动态材料块的本地识别哨兵：日志据此与真实用户 HumanMessage 区分。
# 只使用 id，不使用会进入上游请求的 name，避免额外字段扰动缓存请求结构。
TURN_CTX_ID = "turn-ctx"


def _with_turn_context(window: list, turn_ctx: str) -> list:
    """
    ch07：将动态材料作为临时 HumanMessage 插到滑窗中最后一条用户消息之后。
    这样 ReAct 后续步骤只会在其后追加 AI / Tool 消息，保持已有前缀稳定；
    极端裁剪后找不到用户消息时退化为尾部追加。
    """
    msg = HumanMessage(turn_ctx, id=TURN_CTX_ID)

    # 必须固定插在最近用户消息之后，而不是整个列表末尾：
    # 第 2 步 [Sys, 问, 材料, AI, Tool] 才能完整复用第 1 步 [Sys, 问, 材料] 前缀。
    for i in range(len(window) - 1, -1, -1):
        if isinstance(window[i], HumanMessage):
            return [*window[:i + 1], msg, *window[i + 1:]]

    # 极端裁剪下没有用户消息时安全退化，避免因找不到插入点而中断调用。
    return [*window, msg]


def _agent_messages(state) -> list:
    """
    ch07：拼装主 Agent 实际模型输入：固定 system + 双锚点分层滑窗 + 当前动态材料。
    State 始终保留完整历史，这里只在模型调用前现算精简输入、不回写裁剪结果；
    整条消息列表只保留一条 SystemMessage，且内容恒为 AGENT_SYSTEM。
    """
    # 双锚点将历史渲染为 Layer 1 原文 / Layer 2 半压 / 摘要覆盖三段，
    # 并由 build_window 按当前 token 预算再做最终裁剪兜底。
    window = memory.build_window(state.get("messages", []),
                                 state.get("summary_upto_msg_id") or 0,
                                 state.get("layer1_from_msg_id") or 0)

    # 摘要、检索证据、退款订单数据都属于可变内容；
    # 仅在本次调用时拼成临时用户侧材料，不写回 State["messages"]。
    turn_ctx = _turn_context(state)
    if turn_ctx:
        window = _with_turn_context(window, turn_ctx)

    # system 必须保持唯一且逐字稳定：
    # 部分 chat template 会把所有 system 上提合并，若把可变摘要 / 证据放进 system，会破坏 AGENT_SYSTEM + tools 的稳定缓存前缀；
    # 最终拼接的消息 【系统提示词 | 多轮滑窗历史 | 当前轮用户消息 | 动态材料（早期摘要 + 检索证据/订单数据）| Agent + Tool 消息】
    return [SystemMessage(AGENT_SYSTEM), *window]


def _log_model_context(state, msgs) -> None:
    """
    记录实际送入模型的上下文结构：摘要、真实滑窗、动态材料大小及近似 token 数。
    用于验收切窗与材料注入是否符合预期，只做可观测留痕，不修改实际消息。
    """
    # 动态材料虽然由 HumanMessage 承载，但并非用户真实输入；
    # 借 TURN_CTX_ID 单独摘出，避免日志把它误算成一条滑窗用户消息。
    ctx_block = next((m for m in msgs if getattr(m, "id", None) == TURN_CTX_ID), None)

    # window 只统计真实会话历史，排除固定 system 与临时动态材料块。
    window = [m for m in msgs
              if not isinstance(m, SystemMessage) and getattr(m, "id", None) != TURN_CTX_ID]

    # 每条历史只记录角色和前 40 字：足够核对本轮实际携带了哪些上下文，
    # 同时避免把长回复或工具结果完整灌入日志。
    lines = []
    for m in window:
        text = m.content if isinstance(m.content, str) else ""
        lines.append(f"  [{m.type}] {text[:40]!r}")

    # token 估算继续走 memory.count_tokens，与滑窗裁剪保持同一套中文计数口径。
    # 【示例】：
    #  model_ctx conv=91 step=0 summary='(无)' window=1条 本轮材料=722字 tokens≈399
    #  agent_step conv=91 step=1 input=2223 cache_read=2176 total=2560

    logger.info(
        "model_ctx conv=%s step=%s summary=%r window=%s条 本轮材料=%s tokens≈%s\n%s",
        state.get("conversation_id"), state.get("steps", 0),
        state.get("summary") or "(无)", len(window),
        f"{len(ctx_block.content)}字" if ctx_block else "无",
        memory.count_tokens(msgs), "\n".join(lines),
    )


async def main_agent(state, config=None) -> dict:
    """
    单次 ReAct 推理节点：动态获取当前工具清单并绑定模型，拼装上下文后执行一次模型调用。
    返回 AIMessage 追加到消息历史，并在节点内累加 steps 与 tokens_used。
    注：streaming=True 下 usage 回传已由 get_chat_model 统一配置（ch09 stream_usage=True），此处无需再次 bind。
    """
    # ch08：每轮动态获取内置工具 + MCP 工具合并后的最新清单
    specs = await registry.get_all_specs()
    model = get_chat_model(streaming=True).bind_tools([s.tool for s in specs])

    msgs = _agent_messages(state)
    # ch07：记录摘要 + 滑窗拼装后的实际模型上下文，便于 tail -f 验收
    _log_model_context(state, msgs)

    ai: AIMessage = await model.ainvoke(msgs, config)
    usage = ai.usage_metadata or {}
    used = usage.get("total_tokens", 0)

    # 前缀缓存命中数：用于观察 system + tools 稳定前缀是否被上游实际复用。
    # 上游不返回该字段时记为 0，不代表一定未命中，最终仍以服务商侧数据为准。
    cached = (usage.get("input_token_details") or {}).get("cache_read", 0)

    logger.info("agent_step conv=%s step=%s input=%s cache_read=%s total=%s",
                state.get("conversation_id"), state.get("steps", 0) + 1,
                usage.get("input_tokens", 0), cached, used)

    return {"messages": [ai],
            "steps": state.get("steps", 0) + 1,  # 这种写法看着土，但它明确表达了「读旧值、加一、写回」，比再造一个 reducer 通道好读。
            "tokens_used": state.get("tokens_used", 0) + used}


async def agent_tools(state) -> dict:
    """
    ch08 ReAct 行动节点：处理上一轮 AIMessage 提出的全部 tool_calls，并将执行结果回灌模型。

    普通工具统一交给 engine.execute_tool_call，复用参数校验、权限、超时重试、故障分诊和审计；
    create_ticket 作为写操作，先校验参数，完整时 interrupt 弹确认卡，resume 后再携带可信 confirmed令牌进入执行引擎；
    参数不全则不弹卡，直接由引擎“校验拦下”并引导模型向用户追问。
    ch06 的 submit_refund 保持特殊语义：在本节点拦成前端退款表单，不实际执行工具函数。

    【执行流程分支】
    submit_refund
    → ch06 特例 → 不进 engine → 转前端退款表单

    create_ticket（参数完整）
    → 先 interrupt 等用户确认 → resume 后把 confirmed 可信令牌交给 engine → 真正写操作才可能放行

    其他工具 + 参数不完整的 create_ticket
    → 直接进入 engine → 统一校验 / 权限 / 执行 / 重试 / 分诊 / 审计
    """
    # 本节点只会在 Agent 已产生 tool_calls 后进入，因此最后一条消息就是待处理的 AIMessage。
    last = state["messages"][-1]
    cid = state.get("conversation_id", 0)
    uid = state.get("user_id", "")

    # ch08：执行侧每轮同样动态获取 builtin + MCP 最新清单，
    # 与 main_agent bind_tools 使用同一套 ToolSpec 来源，避免“模型看得到但执行侧找不到”。
    specs = {s.name: s for s in await registry.get_all_specs()}

    # -------------------------- 前置处理：create_ticket 写操作确认 --------------------------
    # 一轮可能出现多个建单 tool call，但确认流只处理第一个。
    ticket_calls = [tc for tc in last.tool_calls if tc["name"] == "create_ticket"]
    tspec = specs.get("create_ticket")
    decision = None

    # 复用 engine 的同一份 JSON Schema 判据：只有参数完整才弹确认卡；
    # 参数缺失时留到下方执行引擎做“校验拦下”，让模型先向用户补问，避免弹出残缺预览。
    if ticket_calls and tspec is not None \
            and engine.validate_args(tspec, dict(ticket_calls[0].get("args") or {})) is None:
        first_args = ticket_calls[0]["args"]

        # interrupt 前只有工具清单读取、参数校验等无业务写副作用操作；
        # resume 时节点会从头重跑，因此这里不会造成重复建单。
        # 用户确认结果最终作为 {"confirmed": bool} 返回给 decision。
        decision = interrupt({
            "type": "confirm_ticket",
            "preview": {
                "ticket_type": first_args.get("ticket_type", "咨询"),
                "description": first_args.get("description", ""),
            },
        })

    tool_msgs = []

    # 保留本轮前序节点已经产生的建议动作，再追加退款表单、订单选择等本节点动作。
    actions = list(state.get("suggested_actions", []))
    not_owned = False  # 本轮是否出现了“不属于当前用户”的订单号，用于末尾补选单入口

    # -------------------------- 逐个处理模型提出的 tool_call --------------------------
    for tc in last.tool_calls:

        # -------------------------- 分支1：submit_refund 特殊拦截（ch06） --------------------------
        # 该设计与普通工具执行不同：详情流程见 tools/builtin/refunds。
        if tc["name"] == "submit_refund":
            # submit_refund 在这里被转换为前端退款表单，不进入统一执行引擎，
            # 因此工具函数内部的 owns_order 当前不会执行，必须在拦截前单独校验归属。
            # 两处校验有意并存：这里保护当前拦截路径，工具内部保护未来可能的直接执行路径。

            # 归属校验失败处理
            if not business.owns_order(
                    uid, str(tc["args"].get("order_id") or "")):
                """
                submit_refund tool call
                → agent_tools 直接截住
                → 根本不进入 engine
                → 所以必须在这里自己做 owns_order
                → 自己生成失败 ToolMessage
                → 同样设置 not_owned
                → 函数末尾统一补 UI
                """
                not_owned = True
                tool_msgs.append(ToolMessage(
                    content="没有找到这位用户的这笔订单,本次不发起退款。请如实告知没查到,"
                            "并让用户从下面列出的订单里选一笔,不要再调用任何工具。",
                    tool_call_id=tc["id"],
                    name="submit_refund",
                    status="error",
                ))
                continue

            # tool call 只被转换成前端退款表单草稿，尚未发生实际退款写操作；
            # 用户最终提交表单后，才由 /api/actions/create-refund 真正落库。
            actions.append({
                "type": "refund_form",
                "draft": {
                    "order_id": tc["args"].get("order_id", ""),
                    "reason": tc["args"].get("reason"),
                },
            })

            # 虽未真正执行 submit_refund，也必须给这次 tool_call 回一条 ToolMessage；
            # 同时明确叫停后续工具调用，避免 ReAct 下一步重复查单或再次申请退款。
            tool_msgs.append(ToolMessage(
                content="已把『提交退款工单』选项交给用户确认。请用一句话说明这一单可以退款并停止,不要再调用任何工具。",
                tool_call_id=tc["id"],
                name="submit_refund",
            ))

        # -------------------------- 分支2：create_ticket 参数完整，已进入确认流 --------------------------
        elif tc["name"] == "create_ticket" and decision is not None:
            if tc is ticket_calls[0]:
                # /api/actions/resume 同时服务订单选择与工单确认，resume_value 形状可能错配；
                # 这里只接受 {"confirmed": bool}，其他形状一律按未确认处理，避免节点直接报错。
                if isinstance(decision, dict) and decision.get("confirmed"):
                    # 用户明确确认后，由节点注入 confirmed=True；
                    # 该令牌不来自模型，因此 engine 的写操作权限门此时才会真正放行。
                    run = await engine.execute_tool_call(
                        tc, cid, specs, confirmed=True, user_id=uid)
                else:
                    # 用户取消或 resume 值异常都走权限拒绝，并用 deny_note 明确告诉模型不要再次建单。
                    run = await engine.execute_tool_call(
                        tc, cid, specs, confirmed=False, user_id=uid,
                        deny_note="用户在工单预览卡片上点了取消,本次不建单。请勿再发起,除非用户再次明确要求。")

                tool_msgs.append(run.tool_message)

            else:
                # 一张确认卡只对应第一个 create_ticket；其余重复申请明确拒绝。
                # 不能直接丢弃：每个 AI tool_call 都必须有对应 ToolMessage，消息协议才完整。
                tool_msgs.append(ToolMessage(
                    content="一次只处理一个建工单请求,本次调用已忽略。",
                    tool_call_id=tc["id"], name="create_ticket",
                    status="error"))

        # -------------------------- 分支3：其余工具统一进入 ch08 执行引擎 --------------------------
        else:
            # 包括参数缺失的 create_ticket：此时 decision=None，不弹确认卡，
            # engine 会先做 JSON Schema 校验并把缺参原因回灌模型，引导其向用户追问。
            run = await engine.execute_tool_call(tc, cid, specs, user_id=uid)

            # query_order 的执行结果由 engine 负责；这里额外记录订单归属失败，
            # 用于末尾给前端补一个本人订单选择器，让用户有继续操作的入口。
            if tc["name"] == "query_order" and not business.owns_order(
                    uid, str((tc.get("args") or {}).get("order_id") or "")):
                """
                → 先交 engine 正常执行
                → query_order 正常返回 NOT_OWNED
                → engine 格式化结果
                → ToolRun(ok=True, status="成功")
                → 写“成功”审计
                → ToolMessage 把“没有找到您的这笔订单”回灌模型
                → → agent_tools 在这里判断一次归属只是为了决定要不要补 UI
                """
                not_owned = True

            tool_msgs.append(run.tool_message)

    # -------------------------- 补充前端建议动作 --------------------------
    # 用户报错/越权订单号时不能只返回“没找到”，同时列出其本人订单供前端点选。
    if not_owned:
        actions.append({
            "type": "select_order",
            "orders": business.list_user_orders(uid),
        })

    # messages 会通过 State 的 add_messages reducer 追加回 ReAct 历史；
    # suggested_actions 则由 runtime 在流结束时整理成 actions 帧交给前端。
    out = {"messages": tool_msgs}
    if actions:
        """
        【actions】传递流程
        agent_tools
            ↓
        actions 临时列表
            ↓
        out["suggested_actions"] = actions
            ↓
        写回 LangGraph State
            ↓
        runtime._stream_events()
            ↓
        收集 upd["suggested_actions"]
            ↓
        流结束时去重
            ↓
        yield {"type": "actions", "items": ...}
            ↓
        API 层转成 SSE
            ↓
        前端收到 actions 事件，渲染表单 / 订单选择器等
        """
        out["suggested_actions"] = actions

    return out


def resolve_answer(state) -> str:
    """
    统一收口本轮最终答复的两种来源：
    确定性节点直接读取 state['answer']；Agent 路径则倒序查找最后一条 AIMessage 并提取文本。
    供接口层、日志层统一调用，无需感知本轮实际经过哪条执行路径。
    """
    if state.get("answer"):
        return state["answer"]

    for m in reversed(state.get("messages", [])):
        if isinstance(m, AIMessage):
            content = m.content
            if isinstance(content, str):
                return content
            return "".join(p.get("text", "") for p in content if isinstance(p, dict))

    return ""


async def log_node(state) -> dict:
    """日志记录:留痕 intent/route/trace(可观测地基),并落 MySQL 一条 assistant 消息(审计)。"""
    logger.info(
        "turn conv=%s intent=%s route=%s resolved=%r trace=%s",
        state.get("conversation_id"), state.get("intent"), state.get("route"),
        state.get("resolved_query"), state.get("trace", {}),
    )
    answer = resolve_answer(state)
    if state.get("conversation_id"):
        await repository.append_message(state["conversation_id"], "assistant",
                                        content=answer or None)
    return {}
