from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # 必填，没有默认值，缺了就在启动时报 chat_model Field required,
    # 调优常量（top_k、步数、阈值这些） 是另一类，它们该留在代码里带默认值。
    chat_model: str

    # 三组上游各自直连,不经网关。base_url 和 key 跟着账号走,所以和 chat_model 一样必填;
    # 嵌入和重排都在硅基流动、地址固定,给了默认值,只有 key 要填。
    chat_base_url: str
    chat_api_key: str
    embed_base_url: str = "https://api.siliconflow.cn/v1"
    embed_api_key: str
    rerank_base_url: str = "https://api.siliconflow.cn/v1"
    rerank_api_key: str

    # 思考链控制。三项都留空就用上游默认,填了才传。
    # 关不关是上游的能力、不是参数写法:同一个 thinking.type,硅基流动的 DeepSeek 和
    # MiniMax-M3 上能关(实测 22.4s/318 token → 1.4s/16),MiniMax-M2.x 上会被静默忽略。
    chat_thinking: str = ""           # disabled 关 / adaptive 开
    chat_reasoning_effort: str = ""   # 开着时的强度:low / high / max
    chat_reasoning_split: str = ""    # MiniMax 专属:true 让思考进 reasoning_content 独立字段,
                                      # 而不是混在 content 里的 <think>。关不掉思考链时靠它兜底
    token_budget: int = 2000
    database_url: str = "mysql+asyncmy://root:root@localhost:3306/mewhelp"
    test_database_url: str = "mysql+asyncmy://root:root@localhost:3306/mewhelp_ch02_test"

    # 前后端分离：同域代理不需要 CORS；直连开发时显式配置允许的浏览器来源。
    # 环境变量使用 JSON 数组，例如 CORS_ORIGINS=["http://localhost:5173"]。
    cors_origins: list[str] = []
    # 分类器可独立容器部署；容器之间使用服务名，不能使用宿主回环地址。
    classifier_url: str = "http://127.0.0.1:8110"

    # ch03 知识库检索
    embed_model: str = "BAAI/bge-m3"          # 上游真实名(无网关别名可用)
    milvus_uri: str = "http://localhost:19530"   # ch04: Lite → Standalone

    # ch04 混合检索 + 重排
    rerank_model: str = "BAAI/bge-reranker-v2-m3"   # 上游真实名
    recall_top_k: int = 50        # dense/BM25 各召回 Top-50
    rerank_top_k: int = 10        # 精排出 Top-10
    subquery_split: bool = True   # 一问多意图时按子句拆开各检索一遍再轮转合并(见 retrieval)
    rerank_min_score: float = 0.3 # 重排最高分低于此 → 检索证据低,拒答

    # ch05 图编排
    max_agent_steps: int = 10              # ReAct 环最大步数 (封顶,超则兜底)

    # token 花销不在环里卡:那是成本控制,归 ch09 跟 Langfuse 的账一起看。
    # tokens_used 仍逐步累加,进 trace 供排障和统计,只是不再当停止条件。
    checkpointer_db_path: str = "data/ch05_checkpoints.sqlite"  # LangGraph checkpointer(data/*.db* 已 gitignore)

    # ch06 意图识别模型配置(先求准:默认大模型;降级路仅种子,本章不实现运行时)
    intent_base_url: str = ""  # 空=回落 chat_base_url
    intent_api_key: str = ""  # 空=回落 chat_api_key
    summary_base_url: str = ""  # 空=回落 chat_base_url
    summary_api_key: str = ""  # 空=回落 chat_api_key
    intent_model: str = ""  # 意图识别模型名;空=回落 chat_model
    intent_small_model: str = ""  # 降级路小模型(种子,未接运行时)
    intent_mode: str = "accuracy"  # accuracy=只用大模型;cost=小模型判→低置信升级大模型(未实现)
    intent_conf_threshold: float = 0.6  # cost 模式升级阈值(种子)

    # -------------------------- ch07 会话上下文管理 --------------------------
    # 基础
    context_window_max_tokens: int = 0  # 滑窗上限；0=由 core/budget 动态计算
    summary_model: str = ""  # 摘要模型；空=复用 chat_model
    model_context_window: int = 0  # 模型窗口；0=按 chat_model 查本地表

    # 总预算
    context_budget_turns: int = 30  # 按 30 轮最坏情况预留历史预算
    max_user_input_tokens: int = 6000  # 单轮用户输入上限
    max_output_tokens: int = 8000  # 单轮输出上限，同时作为 API max_tokens
    system_prompt_tokens: int = 4000  # system + 工具 schema 的预算估值
    evidence_tokens_per_chunk: int = 400  # 单块检索证据的预算估值

    # 三层上下文
    layer1_ratio: float = 0.7  # 层1原文占滑窗预算比例，其余给层2
    layer2_reply_keep_chars: int = 300  # 层2客服答复保留字符数
    layer2_tool_keep_tokens: int = 1000  # 层2工具结果原文保留阈值
    tool_result_max_tokens: int = 5000  # 单条工具结果首次回灌的硬上限
    agent_step_ai_tokens: int = 1000  # 单个 ReAct 中间 AIMessage 预算估值（覆盖 tool_call 参数及可能进入 content 的推理文本）

    # 分段摘要
    summary_inject_segments: int = 3  # 上下文注入最近摘要段数
    summary_tokens_per_segment: int = 2000  # 单段摘要的预算估值

    # 估算余量
    budget_safety_margin: int = 4000  # 给近似计数、消息包装、provider 差异等留的x token误差带
    zh_chars_per_token: float = 1.6  # 中文近似计数：1 字按 1.6 token

    # ch08 工具系统(注册中心 + 执行引擎 + MCP 接入)
    mcp_logistics_url: str = "http://127.0.0.1:8101/mcp"    # 物流 MCP Server
    mcp_aftersales_url: str = "http://127.0.0.1:8102/mcp"   # 售后 MCP Server
    tool_default_timeout: float = 5.0    # 内置工具默认超时(秒)
    mcp_tool_timeout: float = 10.0       # MCP 工具默认超时(走 HTTP,放宽)
    tool_max_retries: int = 2            # 只读工具暂时性故障最大重试次数
    demo_ticket_delay_seconds: float = 0.0  # 验收 6:>0 时 create_ticket 人为变慢(写超时演示)

    # ch09 可观测(Langfuse 自部署;三者齐全才挂回调,缺省时系统照常跑、测试环境不依赖)
    # 环境变量名与课程 README/SDK 一致:LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY / LANGFUSE_BASE_URL
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_base_url: str = ""     # 如 http://localhost:3000(自部署地址,数据不出门)
    # ch09 置信度闸(正式版):阈值由 make calibrate-confidence 在 ch04 评估集上校准回填,不拍脑袋
    # 实测:可答桶 p50=0.715 vs 应拒桶 p50=0.105/max=0.251,YoudenJ 最优点 0.26
    # (可答通过率 85%、应拒放行率 0%,报告见 data/ch09/reports/confidence_calibration.txt,
    #  「观测与成本」页 /observability 上也能看,推荐值与这里不一致时页面会标出来)
    evidence_confidence_threshold: float = 0.26


settings = Settings()
