"""
ch08 · 工具统一执行引擎。

所有工具调用统一经过：查工具 → JSON Schema 校验 → 权限门 → 执行（超时/重试）
→ 故障分诊 → 结果格式化与审计。
核心契约是 never-raises：参数错误、权限拒绝和工具故障都转换成 ToolRun / ToolMessage
回灌 ReAct，而不是让异常直接打断整轮对话；审计失败只记日志，不反向影响工具执行。
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass

import httpx
from jsonschema import Draft202012Validator
from jsonschema.exceptions import best_match
from langchain_core.messages import ToolMessage

from app.config import settings
from app.db import repository
from app.tools.registry import ToolSpec

logger = logging.getLogger(__name__)


# 仅对超时、网络抖动等暂时性故障考虑重试；
# 业务异常 / ToolException 重试通常不会改变结果，因此不在此列。
_TRANSIENT = (asyncio.TimeoutError, TimeoutError, ConnectionError, httpx.TransportError)

# 审计只保存工具结果摘要，避免大块返回值直接灌入日志表。
_SUMMARY_LIMIT = 500


@dataclass
class ToolRun:
    """统一的工具执行结果：既供 agent_tools 判断结局，也携带回灌模型的 ToolMessage。"""

    tool_call_id: str
    name: str
    ok: bool
    tool_message: ToolMessage
    status: str               # 成功 / 失败 / 超时 / 校验拦下 / 权限拒绝
    retry_count: int = 0
    duration_ms: int = 0


def validate_args(spec: ToolSpec, args: dict) -> str | None:
    """
    按 ToolSpec 中模型可见的 JSON Schema 校验 tool call 参数。
    通过返回 None；失败时用 best_match 选取最有代表性的错误并附带字段位置，供模型修正参数或向用户追问。
    agent_tools 也复用它判断 create_ticket 参数是否齐全。
    """
    # `Draft202012Validator.iter_errors(args)`：对`args`做 JSON‑Schema 校验，会产出一整个迭代器，
    #                                           包含全部校验错误（可能好几十条：类型错、缺字段、格式不对等）。
    # `best_match(错误迭代器)`：从一堆校验错误里挑选出最关键、最适合丢给大模型的那 1 条错误对象；
    #                         （默认优先选路径更上层（`path`更短）的错误，认为根源错误在更顶层）；没有错误返回 None。
    err = best_match(Draft202012Validator(spec.json_schema).iter_errors(args))
    if err is None:
        return None

    where = f"(字段 {err.json_path})" if err.json_path != "$" else ""
    return f"{err.message}{where}"


def _timeout_of(spec: ToolSpec) -> float:
    """优先使用工具自己的超时配置，否则按 MCP / builtin 来源选择默认值。"""
    if spec.timeout is not None:
        return spec.timeout
    return settings.mcp_tool_timeout if spec.source == "mcp" else settings.tool_default_timeout


def _summarize(content: str) -> str:
    """截取工具结果摘要供审计留痕，不影响实际回灌给模型的完整结果。"""
    return content if len(content) <= _SUMMARY_LIMIT else content[:_SUMMARY_LIMIT] + "…(截断)"


def _format_content(spec: ToolSpec, result) -> str:
    """
    将不同来源的工具结果统一整理成可回灌模型的字符串。
    MCP 内容块先提取 text；JSON 字符串尽量还原结构，再按可选 format_result 做业务整形；
    普通纯文本保持原样，最终结构化数据统一序列化为中文可读 JSON。
    """
    # MCP adapter 常返回 [{"type": "text", "text": "..."}]，先抽出其中的文本内容。
    if isinstance(result, list) and result and all(isinstance(b, dict) for b in result):
        texts = [b.get("text", "") for b in result if b.get("type") == "text"]
        if texts:
            result = "\n".join(texts)

    # 字符串若本身是 JSON，则恢复为结构化结果供后续格式化；普通文本直接透传。
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except (json.JSONDecodeError, ValueError):
            return result

    # format_result 是工具级结果适配钩子；钩子失败只降级透传原结果，不能把一次已经成功的工具调用误报成执行失败。
    if spec.format_result is not None and isinstance(result, dict):
        try:
            result = spec.format_result(result)
        except Exception:  # noqa: BLE001
            logger.exception("结果格式化钩子异常,降级透传原始结果 tool=%s", spec.name)

    return json.dumps(result, ensure_ascii=False, default=str)


async def _audit(conversation_id, tool_call_id, name, spec: ToolSpec | None, args,
                 result_summary, status, error_message, retry_count, duration_ms) -> None:
    """
    ch08 工具调用审计：成功、失败、超时、校验拦下、权限拒绝五种结局统一留痕。
    先写一行 tool_run 运行日志，便于实时查看工具来源、MCP Server、重试和耗时；
    再尽力写入审计表。审计属于旁路能力，落库失败不得影响工具主流程。
    """
    logger.info("tool_run conv=%s tool=%s source=%s server=%s status=%s retry=%s duration_ms=%s%s",
                conversation_id or "-", name,
                spec.source if spec else "unknown",
                (spec.mcp_server if spec else None) or "-",
                status, retry_count, duration_ms,
                f" err={error_message[:80]!r}" if error_message else "")

    try:
        await repository.insert_tool_audit(
            conversation_id=conversation_id or None,
            tool_call_id=(tool_call_id or None) and tool_call_id[:64],
            tool_name=name[:128],  # 工具名由模型给出，按数据库列宽截断，避免异常输入拖垮整条审计
            tool_source=(spec.source if spec else "builtin"),
            mcp_server=(spec.mcp_server if spec else None),
            arguments=args or None,
            result_summary=result_summary,
            status=status,
            error_message=error_message and error_message[:500],  # 校验错误可能包含超长参数值，同样按列宽收口
            retry_count=retry_count,
            duration_ms=duration_ms,
        )
    except Exception:  # noqa: BLE001
        # 审计只是旁路：数据库异常、表结构问题等都只记日志，不能反向改变工具执行结果。
        logger.exception("审计写入失败(不影响工具执行) tool=%s status=%s", name, status)


async def execute_tool_call(
    tool_call: dict, conversation_id: int, specs: dict[str, ToolSpec],
    *, confirmed: bool = False, deny_note: str | None = None, user_id: str = "",
) -> ToolRun:
    """
    ch08 工具执行统一入口，遵循 never-raises 契约：
    未知工具、参数错误、权限拒绝、超时和真实执行故障都转换成 ToolRun，
    并通过 ToolMessage 把可处理的信息回灌模型，由 ReAct 决定追问、重试其他工具或如实回复。

    confirmed 是写操作的可信确认令牌，只能由 agent_tools 在用户 interrupt 确认后传入；
    它不属于模型生成的 tool call 参数，因此模型自身无法绕过权限门。
    """
    name = tool_call.get("name") or ""
    tc_id = tool_call.get("id") or ""
    args = dict(tool_call.get("args") or {})
    started = time.monotonic()

    # 各分支统一通过该辅助函数构造 ToolRun，保证 ToolMessage 状态和耗时口径一致。
    def _run(ok: bool, content: str, status: str, retry_count: int = 0) -> ToolRun:
        return ToolRun(tool_call_id=tc_id, name=name, ok=ok, status=status, retry_count=retry_count,
                       duration_ms=int((time.monotonic() - started) * 1000),
                       tool_message=ToolMessage(content=content, tool_call_id=tc_id,
                                                name=name or "unknown",
                                                status="success" if ok else "error"))

    # -------------------------- 校验1：工具存在性核验 --------------------------
    spec = specs.get(name)
    if spec is None:
        run = _run(False, f"工具执行失败:未知工具 {name}", "失败")
        await _audit(conversation_id, tc_id, name or "unknown", None, args, None,
                     "失败", f"未知工具 {name}", 0, run.duration_ms)
        return run

    # -------------------------- 校验2：JSON Schema 参数校验  --------------------------
    # 模型提供的参数先统一按模型可见 Schema 校验，错误不抛出，而是明确回灌模型，让它修正调用；
    # 缺失信息应向用户追问，不能自行编造。
    try:
        verr = validate_args(spec, args)
    except Exception as e:  # noqa: BLE001
        # MCP Schema 来自外部 Server，本身也可能畸形（如坏类型、不可解析的 $ref）；
        # 校验器自身报错同样遵守 never-raises 契约，按工具不可用处理。
        logger.exception("参数校验器异常(多为外部 Server 给的畸形 schema) tool=%s", name)
        run = _run(False,
                   f"工具暂时不可用:参数定义异常({type(e).__name__}),请如实告知用户。",
                   "失败")
        await _audit(conversation_id, tc_id, name, spec, args, None, "失败",
                     f"schema 异常 {type(e).__name__}: {e}"[:500], 0, run.duration_ms)
        return run

    # 若有异常
    if verr is not None:
        run = _run(False,
                   f"参数校验未通过:{verr}。请修正参数重新调用;缺少的信息请先向用户追问,不要编造。",
                   "校验拦下")
        await _audit(conversation_id, tc_id, name, spec, args, None,
                     "校验拦下", verr, 0, run.duration_ms)
        return run

    # -------------------------- 校验3：写操作执行权限检查 --------------------------
    # confirmed 是执行层参数而不是模型 tool call 参数；
    # 写工具没有拿到用户确认令牌时一律拒绝，prompt 中的“请先确认”在这里落成硬约束。
    if spec.permission == "write" and not confirmed:
        note = deny_note or "该写操作需要用户确认,未确认前拒绝执行。请勿再次发起,除非用户明确要求。"
        run = _run(False, f"{name} 未执行:{note}", "权限拒绝")
        await _audit(conversation_id, tc_id, name, spec, args, None,
                     "权限拒绝", note[:500], 0, run.duration_ms)
        return run

    # -------------------------- 关键操作1：可信参数注入 + 执行策略 --------------------------
    # conversation_id / user_id 不在模型可见 Schema 中，只能来自系统可信通道。
    # 放在模型参数校验之后再覆盖注入，即使 tool call 私自携带同名字段也不会生效。
    if spec.inject_conversation:
        args["conversation_id"] = conversation_id
    if spec.inject_user_id:
        args["user_id"] = user_id

    # 写操作恒不自动重试：超时只能说明“没有收到成功响应”，不能证明对端没有执行；
    # 自动再调一次可能产生重复工单等副作用。读操作则允许使用全局或工具级重试配置。
    if spec.permission == "write":
        retries = 0
    else:
        retries = settings.tool_max_retries if spec.max_retries is None else spec.max_retries

    tool_timeout = _timeout_of(spec)
    attempt = 0

    while True:
        try:
            result = await asyncio.wait_for(spec.tool.ainvoke(args), timeout=tool_timeout)

            # -------------------------- 关键操作2：工具调用成功，写执行结果和审计信息 --------------------------
            content = _format_content(spec, result)
            run = _run(True, content, "成功", attempt)

            # 每个执行出口都留审计；这里只保存截断摘要，完整 content 仍回灌模型。
            await _audit(conversation_id, tc_id, name, spec, args, _summarize(content),
                         "成功", None, attempt, run.duration_ms)
            return run

        # -------------------------- 异常处理1：暂时性故障 → 重试 --------------------------
        except _TRANSIENT as e:
            # 只对暂时性故障重试；attempt 表示已经实际发生的重试次数。
            if attempt < retries:
                attempt += 1
                logger.warning("工具暂时性故障,第 %s 次重试 name=%s err=%s",
                               attempt, name, type(e).__name__)
                await asyncio.sleep(0.2 * attempt)
                continue

            # 【暂时性故障耗尽重试】
            # “查询结果为空”属于工具正常返回的业务语义，不走这里；
            # 此分支只处理工具真实超时或网络故障，并要求模型如实告知用户。
            is_timeout = isinstance(e, (asyncio.TimeoutError, TimeoutError))
            status = "超时" if is_timeout else "失败"

            run = _run(False,
                       f"工具暂时不可用:{'执行超时' if is_timeout else type(e).__name__},"
                       "请稍后再试或如实告知用户。",
                       status, attempt)
            await _audit(conversation_id, tc_id, name, spec, args, None, status,
                         type(e).__name__, attempt, run.duration_ms)
            return run

        # -------------------------- 异常处理2：业务/未知异常 → 标记失败 --------------------------
        except Exception as e:  # noqa: BLE001
            # 业务异常和其他未知异常不属于“重试可能恢复”的暂时性故障；
            # 直接失败并回灌真实状态，避免无意义重试或让模型编造成功结果。
            logger.exception("工具执行失败 name=%s", name)

            run = _run(False,
                       f"工具暂时不可用:{type(e).__name__}。请如实告知用户,不要编造结果。",
                       "失败", attempt)
            await _audit(conversation_id, tc_id, name, spec, args, None, "失败",
                         f"{type(e).__name__}: {e}"[:500], attempt, run.duration_ms)
            return run

