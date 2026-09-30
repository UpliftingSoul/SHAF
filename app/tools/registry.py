"""
ch08 · 工具注册中心。

统一管理两类工具：
- builtin：服务启动时扫描 app/tools/builtin/，模块导入即完成注册；
- MCP：运行时通过 mcp_client 动态拉取外部 Server 的工具清单。

每个工具统一封装为 ToolSpec：工具名、用途描述、模型可见参数 Schema 用于模型选工具，
权限、超时、重试、可信参数注入、结果格式化等则属于执行侧管理策略。
最终由 get_all_specs() 合并两类工具，供 Agent 绑定和执行引擎使用。
"""

import importlib
import logging
import pkgutil
from collections.abc import Callable
from dataclasses import dataclass

from langchain_core.tools import BaseTool

logger = logging.getLogger(__name__)


# ch08：工具权限由客服系统自身按名称判定，不采信外部 Server 的权限声明。
# 当前未列入 WRITE_TOOLS 的工具一律按只读处理；本章接入的 MCP 工具均为查询类。
WRITE_TOOLS: set[str] = {"create_ticket"}


@dataclass
class ToolSpec:
    """
    工具统一规格：既保存模型选工具所需的信息，也保存执行引擎的管理策略。

    name / description / json_schema 构成模型可见的工具定义；
    其余字段描述工具来源、权限、执行参数注入、超时重试及结果格式化等运行策略。
    """
    name: str
    description: str
    json_schema: dict            # 模型可见参数 Schema；不包含 InjectedToolArg 等可信注入参数
    tool: BaseTool
    permission: str              # "read" | "write"
    source: str                  # "builtin" | "mcp"
    mcp_server: str | None = None
    timeout: float | None = None            # None：由 engine 按工具来源采用默认超时
    max_retries: int | None = None          # None：用 engine 默认值；0：明确禁止重试
    inject_conversation: bool = False       # 执行前由系统注入 conversation_id
    inject_user_id: bool = False            # 执行前注入可信 user_id，不允许模型自行填写
    # 可选结果加工整形（format_result 是回调函数）；None则透传原始结果
    # format_result不为None时的执行流程：工具执行完成 → 拿到原始dict → 回调加工 → 加工后数据交给LLM
    format_result: Callable[[dict], dict] | None = None


_BUILTIN: dict[str, ToolSpec] = {}
_scanned = False


def permission_for(name: str) -> str:
    """按本地 WRITE_TOOLS 白名单确定工具读写权限，未登记名称默认视为只读。"""
    return "write" if name in WRITE_TOOLS else "read"


def _json_schema_of(tool: BaseTool) -> dict:
    """
    提取模型可见的工具参数 JSON Schema。
    MCP 工具的 args_schema 本就是 dict，可直接使用；内置工具优先读取 tool_call_schema（pydantic 模型），
    从而排除 InjectedToolArg 等仅由系统在执行阶段注入、模型不应看到的参数。
    """
    raw = getattr(tool, "args_schema", None)
    if isinstance(raw, dict):
        return raw

    tcs = getattr(tool, "tool_call_schema", None) or raw
    return tcs.model_json_schema()


def spec_from_langchain_tool(tool: BaseTool, *, source: str, mcp_server: str | None = None,
                             timeout: float | None = None, max_retries: int | None = None,
                             inject_conversation: bool = False, inject_user_id: bool = False,
                             format_result: Callable | None = None) -> ToolSpec:
    """
    将 LangChain BaseTool 封装成统一 ToolSpec。
    工具自身提供名称、描述和参数定义；权限与执行策略由注册中心补齐，
    后续 Agent 绑定和 engine 执行均以同一份规格为准。
    """
    return ToolSpec(name=tool.name, description=tool.description or "",
                    json_schema=_json_schema_of(tool), tool=tool,
                    permission=permission_for(tool.name), source=source, mcp_server=mcp_server,
                    timeout=timeout, max_retries=max_retries,
                    inject_conversation=inject_conversation, inject_user_id=inject_user_id,
                    format_result=format_result)


def register(spec: ToolSpec) -> None:
    """注册内置工具；发生重名时保留先注册者并记录告警，避免被后导入模块静默覆盖。"""
    if spec.name in _BUILTIN:
        logger.warning("工具重名,丢弃后注册者 name=%s(先到者保留)", spec.name)
        return
    _BUILTIN[spec.name] = spec


def scan_builtin() -> None:
    """
    ch08 服务启动时扫描 builtin/ 下全部工具模块，依靠模块 import 触发各自的 register()。
    整体扫描幂等；单个模块导入失败只跳过该工具并记录异常，不拖垮其余工具注册。
    """
    global _scanned
    if _scanned:
        return
    _scanned = True

    # 新增内置工具只需放入 builtin/ 并在模块中调用 register()，
    # 无需再维护一份中心工具清单。

    from app.tools import builtin as pkg

    # 遍历 `builtin` 目录下所有 `.py` 模块（不递归子目录），拿到每个模块名。
    for m in pkgutil.iter_modules(pkg.__path__):
        try:
            # 动态导入该模块。
            # 约定：每个工具模块内部执行 `register(xxxToolSpec)`，导入即自动完成工具注册，**不需要手动在中心清单挨个添加**。
            importlib.import_module(f"{pkg.__name__}.{m.name}")
        except Exception:  # noqa: BLE001
            # 单个工具模块损坏时隔离失败，保证其他可用工具仍能正常注册。
            logger.exception("内置工具模块导入失败,跳过 module=%s", m.name)

    logger.info("内置工具注册完成:%s", sorted(_BUILTIN))


def builtin_specs() -> list[ToolSpec]:
    """返回全部已注册内置工具；首次调用时自动完成 builtin/ 扫描。"""
    scan_builtin()
    return list(_BUILTIN.values())


def get_builtin_spec(name: str) -> ToolSpec | None:
    """按名称查询一个内置工具规格，不存在时返回 None。"""
    scan_builtin()
    return _BUILTIN.get(name)


async def get_all_specs() -> list[ToolSpec]:
    """
    ch08 获取当前完整工具清单：先加入内置工具，再现拉 MCP 工具并合并。
    重名时始终保留内置实现，防止外部 Server 的同名工具覆盖本地能力。

    每次调用都重新获取 MCP 清单，使 Server 侧工具变化无需重启即可被发现；
    返回顺序保持稳定，以尽量维持模型请求中 tools 定义的稳定前缀。
    """
    # 延迟导入避免 registry ↔ mcp_client 在模块加载阶段形成循环依赖。
    from app.tools import mcp_client

    # 内置工具先进入结果集，因此与后续 MCP 工具重名时天然由内置实现优先。
    merged: dict[str, ToolSpec] = {s.name: s for s in builtin_specs()}

    for s in await mcp_client.fetch_mcp_specs():
        if s.name in merged:
            logger.warning("MCP 工具与已注册工具重名,丢弃 name=%s server=%s",
                           s.name, s.mcp_server)
            continue
        merged[s.name] = s

    # 不使用 set 或额外排序：工具定义本身处于模型可缓存前缀中，
    # 无意义的顺序抖动会改变请求前缀，降低 prompt cache 的复用机会。
    return list(merged.values())
