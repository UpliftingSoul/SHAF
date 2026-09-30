"""
ch09 · Langfuse 可观测性接入与 trace 标注。

图编译后统一挂载 Langfuse CallbackHandler，即可自动记录 LangGraph / LLM 调用链，业务节点无需逐个添加埋点；
意图识别完成后再通过 tag_intent() 给当前 trace补充 intent metadata/tag，供后续 Cost Control 按意图归因 token 成本。

Langfuse 属于可选增强能力：未配置时所有入口安全 no-op，不导入 SDK、不影响业务运行；
已配置时也显式使用项目 settings 初始化，而不依赖 SDK 自行读取环境变量。
当前适配 langfuse 4.x API。
"""

import logging

from app.config import settings

logger = logging.getLogger(__name__)
_client = None


def langfuse_enabled() -> bool:
    """三个必要配置同时存在时才启用 Langfuse。"""
    return bool(settings.langfuse_public_key and settings.langfuse_secret_key
                and settings.langfuse_base_url)


def _init_client():
    """
    延迟初始化并复用 Langfuse Client 单例。
    只有真正启用可观测性时才导入 SDK，并从项目 settings 显式传入连接配置。
    """
    global _client
    if _client is None:
        from langfuse import Langfuse

        _client = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            base_url=settings.langfuse_base_url,
        )
    return _client


def _make_handler():
    """
    创建 Langfuse 回调处理器。
    CallbackHandler 可以理解为“监听器”：LangChain 每次开始/结束模型调用、工具调用等操作时，
    会自动通知它，Langfuse 再据此把整个 Agent 执行过程记录下来。
    """
    from langfuse.langchain import CallbackHandler

    class _InlineCallbackHandler(CallbackHandler):
        """
        run_inline=True 表示回调跟当前业务代码在同一个异步任务里执行。

        Langfuse 底层用 OTel（OpenTelemetry，一套记录调用链的通用标准）保存 trace 上下文；
        如果回调被丢到另一个异步 task，后面的节点代码可能找不到当前这条 trace，
        导致 tag_intent() 无法把意图标签补进去。
        """
        run_inline = True

    return _InlineCallbackHandler()


def get_langfuse():
    """
    获取已初始化的 Langfuse Client，主要供成本统计等脚本查询 Langfuse API。
    未配置 Langfuse 时返回 None。
    """
    if not langfuse_enabled():
        return None
    return _init_client()


def attach_observability(graph):
    """
    ch09：给整个 LangGraph 挂一次 Langfuse 监听。

    trace 可以理解为“一次用户请求的完整执行记录”，
    里面会串起经过的节点、模型调用、工具调用、耗时和 token 等信息，方便在 Langfuse 页面中排查问题。
    未配置 Langfuse 时直接返回原 graph，不影响正常业务。
    """
    if not langfuse_enabled():
        return graph

    _init_client()
    return graph.with_config({"callbacks": [_make_handler()]})


def tag_intent(intent: str, confidence: float) -> None:
    """
    ch09 将当前意图及置信度写入 Langfuse trace metadata，并添加 intent tag，
    供 Cost Control 后续按意图聚合 token 与成本。
    Langfuse 未启用时直接跳过；标注失败只记录 warning，不影响正常业务流程。
    """
    if not langfuse_enabled():
        return

    try:
        from langfuse import get_client, propagate_attributes

        # propagate_attributes 用来给“当前这次请求的 trace”补充 metadata 和 tag。
        # Langfuse/OTel 内部把一次次具体操作记录成 span；span 可以理解为 trace 里的一个执行步骤。
        #
        # 这里不能只进入上下文后什么都不做：异步执行下，原来的 span 可能已经结束，
        # 已结束的步骤无法再可靠补属性。因此主动创建一个新的 event（一次轻量观测记录），
        # 让下面的 intent / confidence 能确定写进当前 trace。
        with propagate_attributes(
                metadata={"intent": intent, "intent_confidence": f"{confidence:.2f}"},
                tags=[f"intent:{intent}"],
        ):
            get_client().create_event(name="intent-tagged")

    except Exception:
        # 可观测性属于旁路增强：标注意图失败不能反向打断客服业务链路。
        logger.warning("langfuse tag_intent 失败(已忽略)", exc_info=True)



