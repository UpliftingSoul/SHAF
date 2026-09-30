"""
LLM 统一适配层：集中管理聊天模型配置、流式 usage 修正、思考参数与结构化输出。

业务代码通过 get_chat_model() / structured() 获取模型，统一应用模型槽位、输出上限、
temperature、流式 token 用量回传等配置，避免各调用点自行拼接上游参数。

ch09 额外修正一类 OpenAI 兼容通道的流式 usage 问题：流式响应会被拆成多个 chunk
（即一小段一小段的响应数据），部分上游会在每个 chunk 中重复上报“截至当前的累计用量”，
而 langchain-openai 会把各 chunk 的 usage 相加。这里先把累计值转换为逐 chunk 增量，
再交还原有聚合逻辑，避免成本统计和 token 预算被成倍放大。

结构化输出统一使用 function calling，并针对不同模型族的 tool_calls 流式兼容差异，
选择流式或非流式调用；失败只在同一路径原样重试，不降级到约束更弱的 JSON 通路。
"""

import logging

from langchain_core.exceptions import OutputParserException
from langchain_openai import ChatOpenAI
from pydantic import PrivateAttr

from app.config import settings

logger = logging.getLogger(__name__)

_TRUTHY = {"1", "true", "yes", "on"}


# -------------------------- 流式 usage 修正 --------------------------
def _diff_usage(cur: dict, prev: dict) -> dict:
    """
    对两份“累计 usage 快照”逐字段求差，得到当前 chunk 自己新增的 usage。

    cur / prev 都是一整份 usage 字典，而不是单个 token 数：
    外层有 input_tokens、output_tokens、total_tokens 等数值字段，
    也可能包含 input_token_details / output_token_details 等嵌套字典。

    例：
    cur = {
        "input_tokens": 20,
        "output_tokens": 6,
        "total_tokens": 26,
        "input_token_details": {
            "cache_read": 8,
            "cache_creation": 2,
        },
        "output_token_details": {
            "reasoning": 3,
        },
    }

    因此这里要沿相同字段逐层比较：
    数值字段直接做 当前累计 - 上一累计；
    遇到 details 子字典则递归进入，继续对其中每个数值字段做同样的差分。
    """
    out: dict = {}

    for k, v in cur.items():
        # k 是 usage 字段名，v 是当前 chunk 对该字段报告的累计值；
        # 从 prev 中取同名字段，形成“当前快照 vs 上一快照”的对应关系。
        p = prev.get(k)

        if isinstance(v, dict):
            # details 字段本身还是一层字典，不能直接相减；
            # 递归进入这一层，对 cache_read / reasoning 等内部计数逐项求差。
            # 上一快照没有对应子字典时传 {}，其内部字段自然按上一值 0 处理。
            out[k] = _diff_usage(v, p if isinstance(p, dict) else {})

        elif isinstance(v, (int, float)):
            # 到达真正的数值叶子：
            # 当前 chunk 增量 = 当前累计值 - 上一 chunk 累计值。
            # 上一快照没有该字段时视为 0，因此第一份 usage 会完整保留下来。
            previous = p if isinstance(p, (int, float)) else 0
            out[k] = max(0, v - previous)

        else:
            # usage 中若存在非数值元数据，它没有“求增量”的意义，原样保留。
            out[k] = v

    return out


class _CumulativeUsageChatOpenAI(ChatOpenAI):
    """
    修正部分 OpenAI 兼容上游在流式响应中重复累计 token 的问题。

    部分上游会在每个 chunk 中返回“截至当前的累计 usage”，而 langchain-openai
    会继续把各 chunk 的 usage 相加，导致 token 用量被重复统计。
    因此这里先将累计 usage 转成当前 chunk 的增量，再交由父类按原逻辑聚合。

    上游
     ↓
    每个 chunk 给累计 usage
     ↓
    _convert_chunk_to_generation_chunk()
     ↓
    LangChain 将各 chunk usage 相加
     ↓
    最终 AIMessage.usage_metadata
    """

    # 保存上一条带 usage 的 chunk 的“累计用量”，作为下一条 chunk 做差的基准。
    # 使用 PrivateAttr，避免该运行时状态参与 ChatOpenAI 的配置校验与序列化。
    # （PrivateAttr`是 Pydantic V2 提供的私有属性标记函数，用来定义不属于模型字段、不参与校验、不参与序列化的实例内部状态变量。）
    _prev_usage: dict = PrivateAttr(default_factory=dict)

    def _convert_chunk_to_generation_chunk(
        self, chunk, default_chunk_class, base_generation_info
    ):
        """
        在父类处理流式 chunk 前，将上游返回的累计 usage 修正为单 chunk 增量。
        这里只调整 usage，其余消息内容仍交给 ChatOpenAI 原有逻辑处理。
        """
        usage = chunk.get("usage") if isinstance(chunk, dict) else None

        if usage:
            # 当前 usage 与上一份累计 usage 做差，即可得到本 chunk 实际新增的用量。
            prev = self._prev_usage

            # 同一模型实例可能连续处理多次请求；新一条流开始时累计值会重新变小。
            # 此时清空上一请求的基准，避免把两次请求的 usage 错误相减。
            if any(
                isinstance(v, (int, float)) and v < prev.get(k, 0)
                for k, v in usage.items()
                if not isinstance(v, dict)
            ):
                prev = {}

            # 只把累计 usage 替换为本 chunk 增量，chunk 的其他内容保持不变。
            chunk = {**chunk, "usage": _diff_usage(usage, prev)}

            # 保存当前“累计值”供下一条 chunk 做差，不能保存刚计算出的增量。
            self._prev_usage = usage

        # 父类继续完成 chunk 转换与 usage 聚合；此时相加的已是逐 chunk 增量。
        return super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info
        )


# -------------------------- 模型配置入口 --------------------------
def resolve_slot(slot: str = "chat") -> tuple[str, str, str]:
    """
    按用途槽位取得 (模型名, base_url, api_key)。

    chat 使用主聊天配置；intent / summary 等独立槽位允许按项覆盖，
    某一项留空时单独回落 chat 对应配置，因此只接一家上游时无需重复填写。
    """
    if slot == "chat":
        return settings.chat_model, settings.chat_base_url, settings.chat_api_key

    return (
        getattr(settings, f"{slot}_model", "") or settings.chat_model,
        getattr(settings, f"{slot}_base_url", "") or settings.chat_base_url,
        getattr(settings, f"{slot}_api_key", "") or settings.chat_api_key,
    )


def get_chat_model(streaming: bool = False, model: str | None = None,
                   temperature: float | None = None, slot: str = "chat") -> ChatOpenAI:
    """
    创建项目统一使用的 ChatOpenAI 实例。

    slot 决定默认模型/地址/key，model 可临时覆盖模型名；
    temperature 默认 0.3，评估、自检等要求可复现的链路应显式传 0。
    ch09 统一开启流式 usage 回传，使主 Agent 等流式调用也能进入 token / 成本统计。
    """
    slot_model, base_url, api_key = resolve_slot(slot)

    return _CumulativeUsageChatOpenAI(
        model=model or slot_model,
        base_url=base_url,
        api_key=api_key,
        streaming=streaming,

        # ch09：流式调用也请求返回 token usage，供 Langfuse 成本统计与本地预算统一使用。
        stream_usage=True,

        # 输出 token 上限是模型失控时的最后一道保险，避免单次回答无限增长。
        max_tokens=settings.max_output_tokens,

        # 面向用户默认保留少量生成多样性；评估链路显式传 0 排除采样噪声。
        temperature=0.3 if temperature is None else temperature,

        **_thinking_kwargs(),
    )


def _thinking_kwargs() -> dict:
    """
    将项目的三项“思考/推理”配置转换为 ChatOpenAI 初始化参数。

    thinking、reasoning_split 并非标准 OpenAI 参数，通过 extra_body 原样附加到请求体；
    reasoning_effort 是 ChatOpenAI 已支持的标准参数，可直接传入。
    留空的配置不发送，由具体上游使用自身默认值。
    """
    kwargs: dict = {}
    extra: dict = {}

    if settings.chat_thinking:
        extra["thinking"] = {"type": settings.chat_thinking}

    if settings.chat_reasoning_split:
        # 配置文件中的布尔值实际以字符串读取：1 / true / yes / on 均视为开启，
        # 避免只认 "true" 时把用户本意为开启的常见写法错误转换成 False。
        extra["reasoning_split"] = (
            settings.chat_reasoning_split.strip().lower() in _TRUTHY
        )

    if extra:
        kwargs["extra_body"] = extra

    if settings.chat_reasoning_effort:
        kwargs["reasoning_effort"] = settings.chat_reasoning_effort

    return kwargs


# -------------------------- 结构化输出 --------------------------
# function calling：让模型按给定 schema 把结构化参数写进 tool_calls，
# 调用方直接解析这些参数，而不是从普通自然语言正文中猜 JSON。
#
# 本项目固定只走这一条结构化通路：
# * 实测中转的非流式兼容层会把 arguments 拼成 `{}{"field":...}`，流式下则正常，因此 structured 默认使用流式。
# * 少数模型族行为相反：流式时根本不返回 tool_calls，只能强制改走非流式。
# * 不再降级 json_mode / json_schema。结构化失败应暴露给调用点，由各业务场景采用
#   已定义的安全默认值，而不是悄悄切到一条约束更弱、同样可能解析失败的路径。
#
# with_structured_output 失败有两种表现：直接抛解析异常，或响应没有 tool_calls 时返回 None。
# 两种情况都必须在这里统一视为失败，否则 None 会流到业务调用点后才以属性错误等形式炸掉。


# 这些模型族在当前上游实测“流式不返回 tool_calls”，结构化调用必须切换为非流式。
# 后续若新模型出现同类兼容问题，只需在此增加 family 标识。
_NO_STREAM_TOOLCALL_FAMILIES: tuple[str, ...] = ("minimax",)


def needs_non_streaming_tools(
    model: str | None = None, slot: str = "chat"
) -> bool:
    """判断指定模型是否属于“tool_calls 必须非流式获取”的已知模型族。"""
    name = (model or resolve_slot(slot)[0] or "").lower()
    return any(f in name for f in _NO_STREAM_TOOLCALL_FAMILIES)


def structured(schema, *, model: str | None = None, streaming: bool = True,
               temperature: float | None = None, slot: str = "chat"):
    """
    创建项目统一的结构化输出 Runnable。

    schema 定义期望的结构；底层固定使用 function calling，只解析 tool_calls。
    默认流式以绕过当前中转的非流式 arguments 拼接问题；
    已知不支持流式 tool_calls 的模型族自动切为非流式。偶发失败原样重试一次，再失败则把真实异常交给调用点处理。

    temperature 会继续透传到底层模型；评估、自检等“裁判型”调用应传 0。
    """
    from langchain_core.runnables import RunnableLambda

    slot_model, slot_base, _ = resolve_slot(slot)
    name = (model or slot_model or "").lower()

    # 模型兼容性例外集中在一处判断，调用 structured() 的业务代码无需自行分叉。
    if needs_non_streaming_tools(model, slot=slot):
        streaming = False

    m = get_chat_model(
        streaming=streaming,
        model=model,
        temperature=temperature,
        slot=slot,
    )
    fc = m.with_structured_output(schema, method="function_calling")

    def _log(attempt: int, why: str) -> None:
        # 同一模型经不同兼容上游可能表现不同，因此排障日志同时记录模型和 base_url。
        logger.warning(
            "%s 经 %s 的 function calling 第 %s 次没成(%s),%s",
            name,
            slot_base,
            attempt,
            why,
            "原样重试一次" if attempt == 1 else "抛给调用点",
        )

    def _no_tool_calls() -> OutputParserException:
        return OutputParserException(
            f"{name} 经 {settings.chat_base_url} 连着两次都没返回 tool_calls"
        )

    def _run(x):
        # 某些上游偶发一次不返回/解析不出 tool_calls；仅在完全相同的路径上重试一次，
        # 不切换结构化机制，避免把真正的兼容问题隐藏成另一种解析行为。
        for attempt in (1, 2):
            try:
                r = fc.invoke(x)
            except Exception as e:
                _log(attempt, type(e).__name__)
                if attempt == 2:
                    raise
                continue

            # 没有 tool_calls 时解析器可能直接返回 None 而不抛异常，
            # 必须在这里就地转成失败，避免 None 流入调用点后才报无关的属性错误。
            if r is not None:
                return r

            _log(attempt, "响应里没有 tool_calls")

        raise _no_tool_calls()

    async def _arun(x):
        # 异步路径与同步路径保持完全相同的失败和重试语义。
        for attempt in (1, 2):
            try:
                r = await fc.ainvoke(x)
            except Exception as e:
                _log(attempt, type(e).__name__)
                if attempt == 2:
                    raise
                continue

            if r is not None:
                return r

            _log(attempt, "响应里没有 tool_calls")

        raise _no_tool_calls()

    # RunnableLambda 把同步/异步两套实现包装成同一个 LangChain Runnable，
    # 上层因此可以继续统一使用 invoke / ainvoke，直接得到**已经按 schema 解析完成的 Python 对象**
    # 失败则抛出异常，不向下游泄漏 None。
    return RunnableLambda(_run, afunc=_arun)
