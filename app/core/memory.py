"""
ch07 会话内存与双层上下文滑窗。

本模块负责：
- 统一中文场景下的 token / 字符估算口径，并提供历史 token 裁剪；
- 通过数据库消息锚点，把完整 State 按“已摘要 / Layer 2 / Layer 1”动态渲染成模型窗口；
- 在 Layer 1 超预算时计算新的降级边界，控制近期原文的保留范围；
- 将较早的 AI 回复与大块工具结果压缩为 Layer 2，同时保留用户原话和工具调用结构；
- 为指代消解、意图识别和主 Agent 提供不同形态的早期摘要文本。

完整消息历史始终保留在 State 中；这里的切窗与 Layer 2 压缩都只在模型调用前动态计算，
不反向修改原始历史，因此策略调整后可以直接作用于已有会话。
"""

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.messages.utils import count_tokens_approximately, trim_messages

from app.config import settings
from app.core import budget


def count_tokens(messages) -> int:
    """
    按项目统一的中文字符/token 比例估算一批消息的 token 数。

    LangChain 默认 chars_per_token=4.0 更偏英文场景，直接用于中文会明显低估；
    裁剪、分层预算和日志统计统一经过本入口，避免不同模块使用不同计数口径。
    """
    return count_tokens_approximately(
        messages,
        chars_per_token=settings.zh_chars_per_token,
    )


def chars_to_tokens(n_chars: int) -> int:
    """
    按与 count_tokens 相同的口径，将字符数近似换算为 token 数。

    zh_chars_per_token 表示“每个 token 约对应多少字符”，因此换算方向是字符数除以该值；
    字符/token 换算统一收口，避免其他调用点误用相反方向造成系统性预算偏差。
    """
    return int(n_chars / settings.zh_chars_per_token)


def tokens_to_chars(n_tokens: int) -> int:
    """将 token 数按同一估算口径反算为字符数，主要用于面向用户的长度提示。"""
    return int(n_tokens * settings.zh_chars_per_token)


def window_budget() -> int:
    """
    获取当前滑窗可用 token 上限。
    context_window_max_tokens 非 0 时作为显式覆盖/回滚值，否则使用动态上下文预算结果。
    """
    if settings.context_window_max_tokens > 0:
        return settings.context_window_max_tokens
    return budget.compute().sliding


class SessionStore:
    """内存会话存储：维护 session_id -> 消息列表；前期开发阶段临时使用，无持久化。"""

    def __init__(self) -> None:
        self._sessions: dict[str, list[BaseMessage]] = {}

    def get(self, session_id: str) -> list[BaseMessage]:
        return self._sessions.get(session_id, [])

    def append(self, session_id: str, *messages: BaseMessage) -> None:
        self._sessions.setdefault(session_id, []).extend(messages)


def trim_history(messages: list[BaseMessage], max_tokens: int) -> list[BaseMessage]:
    """
    从最新消息向前裁剪历史，使其尽量控制在指定 token 上限内。
    裁剪后从用户消息开始且不拆分单条消息，避免留下失去问题背景的残缺上下文。
    """
    return trim_messages(
        messages, strategy="last", token_counter=count_tokens,
        max_tokens=max_tokens, start_on="human", allow_partial=False)


# -------------------- ch07：双锚点切窗与摘要注入 --------------------

_DB_ID_PREFIX = "db-"


def _db_msg_id(m: BaseMessage) -> int | None:
    """
    从 LangGraph 消息 id 中解析对应的 MySQL 消息 id。

    入口约定 HumanMessage.id=f"db-{msg_id}"，使 State 消息与数据库中的摘要 /Layer 1 边界共用同一坐标系；
    旧 checkpoint 或其他来源的非 db-* id 返回 None。
    """
    mid = getattr(m, "id", None)

    if isinstance(mid, str) and mid.startswith(_DB_ID_PREFIX):
        try:
            return int(mid[len(_DB_ID_PREFIX):])
        except ValueError:
            return None

    return None


def _index_after(messages: list[BaseMessage], msg_id: int) -> int:
    """
    找到第一条数据库 id 大于指定边界的用户消息下标；边界无效或找不到时返回 0。

    切点只落在 HumanMessage 上，保证窗口始终从一轮对话的问题开始，
    不会从失去用户问题背景的 AI / Tool 消息中间截入。
    """
    if not msg_id:
        return 0

    for i, m in enumerate(messages):
        if isinstance(m, HumanMessage):
            did = _db_msg_id(m)
            if did is not None and did > msg_id:
                return i

    return 0


def build_window(messages: list[BaseMessage], summary_upto_msg_id: int,
                 layer1_from_msg_id: int = 0, max_tokens: int | None = None) -> list[BaseMessage]:
    """
    根据摘要边界和 Layer 1 边界，动态构造本次模型调用使用的历史窗口。

    三段关系：
      id ≤ summary_upto_msg_id         已进入摘要，不再放入滑窗
      summary_upto < id ≤ layer1_from  Layer 2，按半压形态渲染
      id > layer1_from                 Layer 1，保留原始消息

    锚点缺失或为 0 时对应切分自然失效，兼容没有新字段的旧会话；
    所有处理只生成本轮模型输入，不修改 State 中保存的完整历史。
    """
    # 第一步：去掉已经由摘要覆盖的远端历史。
    window = messages[_index_after(messages, summary_upto_msg_id):]

    # 第二步：将 Layer 1 边界之前、摘要边界之后的消息动态渲染成 Layer 2。
    if layer1_from_msg_id:
        split = _index_after(window, layer1_from_msg_id)
        if split:
            window = to_layer2(window[:split]) + window[split:]

    # 第三步：双锚点处理后仍按最终 token 预算兜底，防止窗口整体超过模型可用空间（从后往前，尽可能多保留最新消息）。
    trimmed = trim_history(window, max_tokens=max_tokens or window_budget())

    # 单条消息自身超过预算时 trim_messages 可能返回空列表；
    # 此时宁可保留原窗口交给上层处理，也不能向模型提交完全空的历史。
    return trimmed or window


def layer_tokens(messages: list[BaseMessage], summary_upto_msg_id: int,
                 layer1_from_msg_id: int) -> tuple[int, int]:
    """
    分别统计当前 Layer 2 与 Layer 1 的 token 占用。
    轮末上下文维护据此判断近期原文是否超预算、是否需要继续推进降级边界。
    """
    # 滑窗
    window = messages[_index_after(messages, summary_upto_msg_id):]

    # 层划分边界
    split = _index_after(window, layer1_from_msg_id) if layer1_from_msg_id else 0

    return count_tokens(to_layer2(window[:split])), count_tokens(window[split:])


def next_layer1_from(messages: list[BaseMessage], summary_upto_msg_id: int,
                     layer1_budget: int) -> int:
    """
    Layer 1 超预算时，计算新的 Layer 1 起始消息 id。

    从最新消息反向累计 token，超过预算后将边界落到此前最近的数据库消息锚点；
    边界一次移动到目标位置，而不是每轮微调，使两次降级之间的渲染前缀保持稳定，
    有利于维持 prompt cache 命中。
    """
    window = messages[_index_after(messages, summary_upto_msg_id):]
    total = 0

    # 从后往前遍历信息，累积Token
    for i in range(len(window) - 1, -1, -1):
        total += count_tokens([window[i]])

        # 超出预算时
        if total > layer1_budget:
            # 边界必须落到当前超限位置之前最近的用户消息锚点，
            # 使后续 _index_after() 仍从完整对话轮次开始切分。
            for j in range(i, -1, -1):
                did = _db_msg_id(window[j])
                if did is not None:
                    return did

            return 0

    return 0


def summary_line(summary: str | None) -> str:
    """将早期摘要转成 coref / 意图识别可直接拼接的单行前缀；无摘要返回空串。"""
    return f"(早前对话摘要:{summary})" if summary else ""


def summary_system(summary: str | None) -> SystemMessage | None:
    """
    将早期摘要包装成带标题的消息对象；无摘要返回 None。

    虽然返回类型是 SystemMessage，主 Agent 实际只取其 content 并合入本轮动态 HumanMessage；
    不额外注入第二条 SystemMessage，以保持系统提示 / 工具定义所在的稳定前缀结构。
    """
    if not summary:
        return None

    return SystemMessage(
        f"## 早前对话摘要(更早轮次已压缩,其中事实可信)\n{summary}"
    )


# -------------------- ch07：Layer 2 半压缩渲染 --------------------
# Layer 2 优先保留用户原话中的实体与指代线索；AI 回复只留结论头部，
# 工具结果则按大小决定原样保留还是压成标记，从主要占用项中回收上下文空间。


def compress_reply(text: str, keep_chars: int | None = None) -> str:
    """
    将客服回复压成 Layer 2 形态。
    短回复保持原样；长回复保留头部结论并追加省略标记。
    """
    n = settings.layer2_reply_keep_chars if keep_chars is None else keep_chars

    if not text or len(text) <= n:
        return text

    return text[:n] + "…(略)"


def compress_tool_result(name: str, content: str) -> str:
    """
    将工具结果压成 Layer 2 形态。

    小结果通常仍包含后续追问需要的订单等结构化信息，因此原样保留；
    大块检索证据等一次性结果只留下调用标记，需要时由后续轮次重新实时查询。
    """
    if not content:
        return content

    if chars_to_tokens(len(content)) <= settings.layer2_tool_keep_tokens:
        return content

    return f"(已调用 {name or '工具'},结果从略)"


def to_layer2(messages: list[BaseMessage]) -> list[BaseMessage]:
    """
    将一批历史消息渲染为 Layer 2 半压形态。

    用户消息保持原样，AI 正文和工具结果分别执行对应压缩；消息 id 与工具调用关系继续保留。
    整个转换不修改输入消息，同一批消息重复渲染得到稳定结果。
    """
    out: list[BaseMessage] = []

    for m in messages:
        if isinstance(m, HumanMessage):
            # 用户消息完整保留（这使得实体、订单号和“那个/这双”等指代信息均不丢失）
            out.append(m)

        elif isinstance(m, AIMessage):
            text = m.content if isinstance(m.content, str) else ""

            # 只压 AI 正文，不能丢 tool_calls：后续 ToolMessage 仍通过 tool_call_id 引用它。
            # 若重建 AIMessage 时漏掉该结构，上游会因找不到对应 tool call 直接返回 400。
            out.append(
                AIMessage(
                    compress_reply(text),
                    id=m.id,
                    tool_calls=list(m.tool_calls or [])
                )
            )

        elif isinstance(m, ToolMessage):
            text = m.content if isinstance(m.content, str) else str(m.content)

            # 保留原 tool_call_id、name 与消息 id，只替换工具结果正文的呈现形态。
            out.append(
                ToolMessage(
                    compress_tool_result(
                        getattr(m, "name", ""),
                        text,
                    ),
                    tool_call_id=m.tool_call_id,
                    name=getattr(m, "name", None),
                    id=m.id,
                )
            )

        else:
            out.append(m)

    return out
