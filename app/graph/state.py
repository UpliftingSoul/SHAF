from typing import Annotated
from typing_extensions import TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


def merge_dict(a: dict | None, b: dict | None) -> dict:
    """trace专用合并reducer：字典增量合并，同key后者覆盖前者。
    特殊约定：b=None为重置哨兵。 （哨兵：**特殊约定标记值**，不是正常业务数据，专门用来触发一段特殊逻辑）
    - 仅入口_graph_input传入trace=None，用于每轮会话开始清空trace；
      传入空字典{}无法清空，merge(旧, {})仍保留旧数据，会造成上一轮trace字段跨轮残留。
    - 图内所有业务节点只返回普通dict，禁止传入None，避免误清空全部留痕。
    """
    if b is None:
        return {}
    # `**字典`：把字典**拆成一堆 key=value**，合并到新字典里面。
    # 将 a（为空则用空字典）与 b 字典合并，b 的键覆盖 a 的同名键并返回新字典。
    return {**(a or {}), **b}


# * total=False 让所有字段都变成可选的，于是节点只需要返回自己改动的那几个键，不用每次构造一份完整 State
class ConversationState(TypedDict, total=False):
    # Annotated + add_messages：reducer追加模式；节点返回增量消息，自动合并，不覆盖完整列表
    messages: Annotated[list[AnyMessage], add_messages]  # 跨轮历史,checkpointer 续接
    summary: str              # ch07 早期轮次滚动摘要(入口每轮从 conversations 加载)
    summary_upto_msg_id: int  # 摘要覆盖到的 MySQL 消息 id；其后的历史才进入分层滑窗
    layer1_from_msg_id: int   # 层1原文起点；该 id 之前、尚未进摘要的历史按层2半压形态渲染
    user_id: str
    conversation_id: int

    intent: str            # 九类之一(ch08 增「人工」)
    resolved_query: str    # 指代消解+改写后的完整问句(下游检索/判意图都用它)
    intent_confidence: float  # 意图 JSON 的 confidence(0-1)

    order_id: str          # ch06-refund_flow: 抽取/点选交互 回填的订单号
    order_data: dict       # ch06-refund_flow: query_order 查到的订单数据

    route: str             # knowledge | business | complaint | chitchat

    evidence: str          # 知识链路：拼接完毕、送入LLM生成的证据文本
    citations: list        # 结构化引用chunk，供前端跳转展示
    evidence_strong: bool  # 生成前两道证据闸综合布尔信号，True代表证据充足
    evidence_confidence: float  # ch09 正式置信度闸：多路信号加权综合分数(0-1)
    fallback_source: str        # ch09 兜底落池 source:retrieval_low_conf | self_check
    retrieved_snapshot: list    # ch09 召回快照(Top3 原文+得分):走了检索就有,落池/👎回捞共用

    answer: str            # 确定性节点产出的答复 (Agent 流式输出不写入此字段)

    steps: int             # ReAct 迭代步数(停止条件)，用于循环停止条件判断
    tokens_used: int       # 会话 token 消耗累计，用于预算管控

    suggested_actions: list  # [{"type": "transfer_human"} | {"type": "create_ticket", "draft": {...}}]

    # Annotated + merge_dict：字典reducer增量合并；节点返回局部trace片段，自动合并，不整体覆盖
    trace: Annotated[dict, merge_dict]  # 留痕
