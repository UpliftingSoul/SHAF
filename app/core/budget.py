"""
ch07 上下文预算：根据模型窗口与各类上下文开销，动态计算滑窗真正可用的 token 空间。

一次请求的上下文主要分为两部分：
- 固定开销：系统提示 / 工具 schema、检索证据、摘要、输出预留与安全余量；
- 动态开销：当前轮 ReAct 瞬时峰值，以及可保留的历史滑窗。

这里必须区分两个不同口径：
- turn_peak_tokens()：当前这一轮执行时的瞬时峰值，用于判断本轮能否安全跑完；
- history_per_turn()：一轮沉淀为历史后的稳态占用，用于估算滑窗能覆盖多少轮。

滑窗预算最终取“目标历史轮数所需空间”和“模型窗口扣除固定开销、本轮峰值后的剩余空间”
两者较小值。模型窗口优先使用显式配置，否则按模型名查本地映射，查不到时使用保守默认值。
"""

from dataclasses import dataclass

from app.config import settings


# OpenAI 兼容的 /v1/models 无法提供上下文窗口大小，因此本地维护模型名映射。
# 上游模型名可能带 provider 前缀，lookup_window() 会先剥离前缀，再按最长前缀匹配。
_KNOWN_WINDOWS: dict[str, int] = {
    "minimax-m3": 1_000_000,
    "minimax-m2": 204_800,
    "deepseek-v4-flash": 1_048_576,
    "deepseek-v4": 1_048_576,
    "deepseek-v3": 131_072,
    "deepseek-chat": 131_072,
    "deepseek-reasoner": 131_072,
    "qwen3": 131_072,
    "qwen-max": 32_768,
    "glm-4": 131_072,
    "kimi-k2": 262_144,
    "moonshot-v1-128k": 131_072,
    "gpt-4.1": 1_047_576,
    "gpt-4o": 128_000,
    "claude-": 200_000,
}

# 未知模型宁可按较小窗口估算，避免预算看似健康、实际请求却撞上下文上限。
_FALLBACK_WINDOW = 128_000


def lookup_window(model: str) -> tuple[int, bool]:
    """
    按模型名查找上下文窗口，返回 (窗口大小, 是否命中映射)。
    支持 deepseek-ai/DeepSeek-V4-Flash 这类带 provider 前缀的模型名；
    未命中时返回保守默认窗口。
    """
    name = (model or "").strip().lower().rsplit("/", 1)[-1]

    # 使用最长前缀匹配，避免较短的同系列名称抢先命中更具体的模型。
    hit = max(
        (k for k in _KNOWN_WINDOWS if name.startswith(k)),
        key=len,
        default=None,
    )
    return (_KNOWN_WINDOWS[hit], True) if hit else (_FALLBACK_WINDOW, False)


def resolve_window() -> int:
    """
    解析当前模型上下文窗口。
    优先级：显式配置 model_context_window > 模型名映射 > 保守默认值。
    """
    if settings.model_context_window > 0:
        return settings.model_context_window
    return lookup_window(settings.chat_model)[0]


@dataclass(frozen=True)
class ContextBudget:
    """
    一次模型请求的上下文预算分解。

    window / fixed / sliding / peak 均以 token 为单位；
    turns 表示当前滑窗预算理论上可覆盖的历史轮数，
    healthy 供启动自检判断当前配置是否至少容得下一轮正常运行。
    """

    window: int      # 模型上下文容量
    fixed: int       # 固定开销
    sliding: int     # 滑窗容量（layer 1 + layer 2 使用）
    turns: int       # sliding 按历史单轮稳态占用（layer 2 单轮）折算出的理论覆盖轮数；不等同于 Layer 1 原文实际保留轮数
    peak: int        # 当前轮 多步React 的token消耗上限
    limited_by: str  # 滑窗容量的计算口径依据
    healthy: bool    # 本轮请求是否可以顺利走完

    @property
    def per_turn(self) -> int:
        """历史中单轮对话的稳态 token 占用；当前轮最坏情况应看 peak。"""
        return history_per_turn()


def summary_tokens() -> int:
    """按摘要注入段数与单段预算，计算请求中需要预留的摘要 token。"""
    return settings.summary_inject_segments * settings.summary_tokens_per_segment


def evidence_tokens(top_k: int | None = None) -> int:
    """
    按最终注入的证据条数估算检索证据预算。
    默认跟随 rerank_top_k，调整精排保留数量时预算同步变化。
    """
    k = settings.rerank_top_k if top_k is None else top_k
    return k * settings.evidence_tokens_per_chunk


def turn_peak_tokens() -> int:
    """
    估算当前一轮 ReAct 执行过程的瞬时峰值，不含固定开销和历史滑窗。

    最多执行 max_agent_steps 步，每步产生的 AI tool-call 消息与工具结果都要继续挂在消息列表中供下一步使用；
    最终回答所需输出空间已在 fixed 中单独预留，无需再乘步数。
    """
    return settings.max_user_input_tokens + settings.max_agent_steps * (
        settings.tool_result_max_tokens + settings.agent_step_ai_tokens
    )


def history_per_turn() -> int:
    """
    估算一轮对话沉淀进历史后的稳态 token 占用。

    历史工具结果按 Layer 2 的保留上限计算，而不是继续使用本轮执行时的原始峰值；
    否则会为后续必然被压缩掉的大块工具结果长期预留空间，显著缩短滑窗覆盖轮数。
    """
    return (
        settings.max_user_input_tokens
        + settings.max_output_tokens
        + settings.max_agent_steps * settings.layer2_tool_keep_tokens
    )


def compute(window: int | None = None) -> ContextBudget:
    """
    根据当前配置计算一次完整上下文预算，不执行任何 IO。

    先从模型窗口中扣除固定开销和当前轮峰值，得到真正可分配给历史的空间；
    再与目标历史轮数对应的稳态预算取较小值，得到最终 sliding。
    """
    w = resolve_window() if window is None else window
    per_turn = history_per_turn()
    peak = turn_peak_tokens()

    # 固定块包括系统提示/工具 schema、检索证据、摘要、最终输出预留和估算安全余量。
    fixed = (
        settings.system_prompt_tokens
        + evidence_tokens()
        + summary_tokens()
        + settings.max_output_tokens
        + settings.budget_safety_margin
    )

    # 当前轮 ReAct 峰值必须先扣：sliding 只能使用本轮正常执行之后真正剩余的窗口。
    # 若省掉这一项，小窗口可能在预算阶段显示健康，却在多步工具调用过程中实际溢出。
    room = w - fixed - peak   # 1,000,000 - 固定开销 - [6000 + 6 * (5000 + 1000)]
    if room <= 0:
        return ContextBudget(
            w, fixed, 0, 0, peak, "window", False
        )

    # 轮数项使用历史稳态而非本轮峰值：前者回答“能保留几轮”，后者只负责“本轮能否跑完”。
    by_turns = settings.context_budget_turns * per_turn   # 30 * (6,000 + 8,000 + 1,000) = 450,000
    sliding = min(by_turns, room)

    limited_by = "turns" if sliding == by_turns else "window"
    turns = sliding // per_turn

    return ContextBudget(
        w, fixed, sliding, turns, peak, limited_by, turns >= 1
    )


def describe(b: ContextBudget) -> str:
    """将预算结果格式化为启动日志 / 自检使用的一行可读说明。"""
    return (
        f"窗口={b.window} 固定开销={b.fixed} 单轮峰值={b.peak} 滑窗={b.sliding} "
        f"覆盖轮数={b.turns}(历史每轮 {b.per_turn}) 受限于={b.limited_by} "
        f"健康={'是' if b.healthy else '否'}"
    )
