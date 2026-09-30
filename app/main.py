"""SHAF 纯 API 入口：注册业务路由和运行生命周期，页面由独立 Vue 服务提供。"""
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.acceptance import router as acceptance_router
from app.api.actions import router as actions_router
from app.api.admin import router as admin_router
from app.api.agent import router as agent_router
from app.api.chat import router as chat_router
from app.api.conversations import router as conversations_router
from app.api.extract import router as extract_router
from app.api.feedback import router as feedback_router
from app.api.jobs import router as jobs_router
from app.api.kb import router as kb_router
from app.api.observability import router as observability_router
from app.api.rageval import router as rageval_router
from app.api.review import router as review_router
from app.api.topics import router as topics_router
from app.graph import runtime
from app.config import settings


# 获取业务专属logger实例，命名空间app，与uvicorn内置日志隔离
_applog = logging.getLogger("app")
# 避免重复添加处理器；仅首次无handler时初始化配置
if not _applog.handlers:
    # 日志格式：级别:日志器名:日志内容
    _fmt = logging.Formatter("%(levelname)s:%(name)s:%(message)s")

    # 控制台输出处理器，uvicorn默认不会为业务logger挂载INFO处理器，不加则业务info日志无输出
    _h = logging.StreamHandler()
    _h.setFormatter(_fmt)
    _applog.addHandler(_h)

    # 文件输出：持久化业务日志到log/app.log，作为tail查看目标
    # propagate=False隔离uvicorn HTTP访问日志噪声，git已忽略该日志目录
    _log_dir = Path(__file__).resolve().parent.parent / "log"
    _log_dir.mkdir(parents=True, exist_ok=True)  # 目录不存在自动创建
    _fh = logging.FileHandler(_log_dir / "app.log", encoding="utf-8")
    _fh.setFormatter(_fmt)
    _applog.addHandler(_fh)

_applog.setLevel(logging.INFO)
# 禁止日志向上传递到根logger，防止业务日志和uvicorn的http访问日志混杂
_applog.propagate = False

# 当前模块logger，继承app日志配置
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    启动预热 Milvus：加载集合并轮询直到可检索，避免首个用户请求撞上 Standalone 的加载竞态
    （集合 load 在后台异步完成，未就绪时检索会静默返回空）。预热失败不阻塞启动。
    """
    try:
        from app.core import retrieval

        for _ in range(15):
            hits = await retrieval.search_knowledge(
                "退货运费", strategy="bm25", top_k=1
            )
            if hits:
                logger.info("Milvus 预热完成,集合可检索")
                break
            await asyncio.sleep(1)
        else:
            logger.warning("Milvus 预热超时(15s)未返回结果,继续启动")
    except Exception:
        logger.warning("Milvus 预热异常,继续启动", exc_info=True)

    from app.tools import registry as tool_registry
    # ch08：扫描 builtin/ 并通过 import 完成内置工具注册。
    tool_registry.scan_builtin()

    # ch07：图初始化前检查当前模型与配置的上下文预算是否健康。
    _check_context_budget()

    await runtime.init_graph()
    yield
    await runtime.close_graph()


def _check_context_budget() -> None:
    """
    ch07 启动上下文预算自检：按当前模型窗口与配置计算一次完整预算。
    healthy 时记录预算分解；预算不足时打 ERROR 并提示主要调节项，
    但不主动抛异常阻断服务启动。
    """
    from app.core import budget

    b = budget.compute()

    if b.healthy:
        logger.info("上下文预算 %s", budget.describe(b))
    else:
        logger.error(
            "上下文预算不足:窗口 %s 装不下 固定开销 %s + 单轮 ReAct 峰值 %s。"
            "换窗口更大的模型,或调小 MAX_AGENT_STEPS / TOOL_RESULT_MAX_TOKENS / "
            "MAX_OUTPUT_TOKENS / RERANK_TOP_K。%s",
            b.window,
            b.fixed,
            b.peak,
            budget.describe(b),
        )


app = FastAPI(title="SHAF", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type"],
)


@app.get("/api/health", tags=["运维"])
async def health() -> dict:
    """进程存活检查；不代表 MySQL、Milvus 或上游模型已经可用。"""
    return {"status": "ok", "service": "SHAF"}


app.include_router(actions_router)
app.include_router(chat_router)
app.include_router(extract_router)
app.include_router(agent_router)
app.include_router(conversations_router)
app.include_router(feedback_router)
app.include_router(review_router)
app.include_router(topics_router)
app.include_router(acceptance_router)
app.include_router(kb_router)
app.include_router(rageval_router)
app.include_router(observability_router)
app.include_router(admin_router)
app.include_router(jobs_router)
