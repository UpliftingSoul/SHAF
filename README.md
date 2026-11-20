# SHAF: Stable Hybrid Agentic Framework for E-Commerce Support

SHAF 是面向电商场景的智能客服 Agent 框架，整合了一套完善的 Harness Engineering 工程化解决方案，核心模块包含 Graphic Workflow、ReAct Loop、Prompt 模板库、指代消解、意图识别、向量知识库、RAG 混合检索与生成校验链路、BuiltIn + MCP 工具系统与安全执行引擎、分层上下文管理、成本统计与优化、数据飞轮、可观测 Trace、自调主题分类器等，具备稳定可控、低开销高可用、透明可审计、自进化等关键优势。

## 核心设计与亮点

- **执行编排**：采用 Graphic Workflow + ReAct Loop 混合架构。基于 LangGraph 实现图编排、状态更新与流转、记忆持久化 Ckeckpointer、流式 Runtime、人工审核 HITL 与断点续跑等系统核心能力；收到一轮请求时，先完成指代消解与意图识别，然后根据意图路由至对应执行路径（如商品咨询强制走知识检索，退款处理先取订单再据此查政策最后交由Agent处理等）。Agent 主节点采用 ReAct 工作范式，通过多步的思考与工具调用解决问题；采用了预算管理、终止校验、可观测配置、统一 ToolRun Engine 等 Loop Engineering 策略模块，全面提高 ReAct 的稳定性与可控性。
- **Advaned RAG**：容器化部署 Milvus 向量库，支持 HNSW 高效索引与多路检索，知识更新及维护便捷。离线建库环节走文档解析 → 多策略切块 → 向量化→ 双写入库执行路线；在线检索与生成阶段采用查询优化→ 向量语义与 BM25 关键词双路检索 → RRF融合 → Cross-Encoder 重排 → 三道证据置信检验工作链路。在覆盖各业务类型的分层评估题集上开展了 RAG 基础链路与生产链路评估：基础链路实测可答题 Recall@5 已达 98%，Faithfulness 高达 100 %；生产链路实测可答题 Recall@5 提升至 100%，应拒答题放行率为 0%，综合幻觉率 0%。
- **工具系统**：内置工具用于执行本地数据库操作，MCP 服务提供物流信息等三方数据的查询能力，进入 Agent 节点时动态拉取可用工具清单。调用走统一的执行入口，对参数校验错误、权限检查不通过、多次重试失败等各类异常进行合理处理，包装 ToolMessage 对象供模型进行下一步推理，并将审计记录留档便于后台排查问题。工具遵循原子化设计，权限类型划分明确，具备 write 权限的工具执行前严格走人工确认和幂等校验，可信鉴权信息由应用层注入，从根本上抵御提示词注入攻击，确保各项操作安全稳定。
- **上下文管理**：采用三层上下文历史结构，分别为早期摘要层、较早半裁层和近期原文层。请求收尾阶段根据预算设定动态重置分层，各层超预算部分降级处理（选择性裁剪 & 异步摘要）。旨在在有限的上下文空间中存储更多有效信息，实现成本与长对话表现的同步优化，且引入的额外计算开销与延迟可忽略。此外，每轮请求处理前上下文按最优顺序拼接，最长同前缀实现最大化 KV Cache 复用，进一步降低 Token 开销。
- **数据飞轮**：生成前证据强度校验不通过或用户反馈未解决的问题存入低置信度问题池，经过标准化与归并处理后进入数据飞轮待审队列。每条待审问题附带入池原因、当时的检索片段、LLM模拟解答等重要参考信息，由人工确认入库必要性，填写标答并确认会把 QA 对转化为全新的知识 Chunk，高效执行双写入库链路。数据飞轮设计使系统在上线后可持续填补知识缺口，实现自进化。
- **观测与维护**：在图编译后统一挂载 Langfuse 回调，以零侵入方式实现Trace可观测，避免手动埋点带来的开发与维护代价。Langfuse 为每次请求自动生成嵌套调用树，清晰记录各节点输入输出、Token 开销和耗时，对话消息、检索结果、工具调用均清晰可查，配合应用层的服务日志与异常记录，实现精准的故障定位。各 Trace 绑定意图标签，支持按请求意图统计开销以明确链路成本优化方向。

## 技术栈

| 层次 | 主要技术 |
| --- | --- |
| 前端 | Vue 3 Composition API、TypeScript、Vue Router、Vite |
| API 与编排 | FastAPI、Pydantic、LangChain、LangGraph |
| 存储与检索 | SQLAlchemy、MySQL、SQLite checkpoint、Milvus、BM25/RRF、重排 |
| 工具与观测 | MCP、统一工具执行引擎、Langfuse |
| 分类与部署 | RoBERTa 多标签分类、ONNX Runtime、uv、Docker、Nginx |

聊天、嵌入和重排上游独立配置，通过 `.env` 中的 `CHAT_*`、`EMBED_*`、`RERANK_*` 切换模型与地址。密钥与业务依赖配置只保留在后端。

## 架构概览

浏览器访问 Vue 页面；开发时由 Vite、生产时由 Nginx 提供页面并代理 `/api`。FastAPI 负责业务 API、流式对话和图运行生命周期，调用 MySQL、Milvus、模型与 MCP 服务。MySQL 保存业务记录，SQLite checkpoint 保存图线程与中断状态；报表和分类器承担独立评估及知识维护职能。

生产使用 Ubuntu 云端容器部署，Vue/Nginx 与 FastAPI 分别运行；MySQL、Milvus Standalone（含 etcd/MinIO）、两个 MCP 和 ONNX 分类器独立运行，Langfuse 使用云端。本地只做开发。生产入口为 `docker-compose.prod.yml`，含 CPU 训练镜像与分阶段初始化步骤；真实服务器构建和业务验收按 [部署手册](docs/DEPLOY.md)执行。

当前部署手册以个人单机测试/展示为主：1Panel 管理容器和文件，前端保留标准 Nginx；通过 SSH 隧道或受来源 IP 限制的公网 IP 访问。正式上线的域名、HTTPS、认证及长期运维保留为选读内容。

| 手册 | 用途 |
|---|---|
| [部署](docs/DEPLOY.md) | 从空服务器准备到首次服务验收 |
| [运维](docs/operations.md) | 数据库、日志、trace、调度、备份恢复与更新 |
| [快速修正](docs/quick-fixes.md) | 个人调试中的局部修补、适用边界及镜像固化 |
| [网站使用与测试](docs/testing-guide.md) | 十二个页面及核心业务链路的操作与判据 |
| [Milvus 云端备用规划](docs/milvus-cloud-migration-plan.md) | 后续切换云端的最小改动、索引迁移与回退 |

## 本地开发

先复制 `.env.example` 为 `.env`，填写模型配置及开发机可访问的依赖服务地址。数据库、Milvus、MCP、分类器与 Langfuse 按云端容器约定配置；未就绪的依赖会影响对应功能。真实密钥不提交 Git。

```bash
uv sync --locked --python 3.12
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

另开终端启动前端（Node.js 22.12+）：

```bash
cd frontend
npm ci
npm run dev
```

浏览器打开 <http://127.0.0.1:5173>，Vite 将 `/api` 代理到后端。<http://127.0.0.1:8000/docs> 提供接口文档；8000 端口不提供业务页面。`uv` 使用项目独立 `.venv`，不向 conda base 安装依赖。

开发代理地址可在 `frontend/.env` 中通过 `API_PROXY_TARGET` 调整，该变量不打包进浏览器。`make dev` / `scripts/dev.sh` 是本机多进程编排入口，不作为云端独立容器的部署流程。

## 目录与职责

| 位置 | 职责 |
| --- | --- |
| `frontend/` | 十二个 Vue 页面、共享组件、组合式状态、API/SSE 客户端、测试及 Nginx 配置 |
| `app/api/` | 聊天、业务动作、知识、审核、报表与任务的 HTTP 入口 |
| `app/graph/` | LangGraph 状态、节点、分流规则和图组装 |
| `app/core/` | 模型调用、检索、重排、意图、指代、上下文、置信度、飞轮与观测 |
| `app/kb/` | 知识切块、嵌入、双写、去重及问答候选挖掘 |
| `app/tools/` | 内置工具、MCP 客户端、注册表和统一执行引擎 |
| `app/db/` | 业务表模型与仓储 |
| `mcp_servers/` | 物流与售后 MCP 服务 |
| `sql/` | 建表、种子数据及模块迁移；MySQL 空卷首启按文件名执行 |
| `scripts/` | 数据初始化、离线评估、知识维护与分类训练 |

逐文件说明见 [架构手册](docs/architecture.md)，前后端分离决策和验证边界见 [改造规划](docs/frontend-separation-plan.md)。数据库、卷名及浏览器身份键保留现有兼容标识，避免因项目展示名称调整导致数据迁移。

## 模块与验证入口

`ch01–ch10` 是模块分组编号。`make help` 提供完整目标和说明；离线任务须具备相应服务、数据和运行环境，涉及 make 的任务在 Ubuntu 环境执行。

| 模块 | 能力 | 验证或维护入口 |
| --- | --- | --- |
| ch01 | 流式对话、结构化提取 | `make eval` |
| ch02 | 业务工具与 Function Calling | `make eval-agent` |
| ch03 | 切块、双写、知识挖掘 | `make kb-build`、`make kb-vectorize`、`make eval-retrieval` |
| ch04 | 混合检索、重排及四策略评估 | `make smoke-rag`、`make eval-rag` |
| ch05 | Workflow 骨架与 ReAct 执行 | `make eval-ch05` |
| ch06 | 意图分流、指代消解、中断续跑 | `make smoke-interrupt`、`make eval-ch06` |
| ch07 | 分层上下文、摘要与前缀缓存 | `make eval-ch07` |
| ch08 | MCP、工具执行引擎与审计 | `make eval-ch08` |
| ch09 | 可观测、飞轮与成本报告 | `make flywheel`、`make eval-flywheel`、`make cost-report` |
| ch10 | 主题语料、微调、阈值与 ONNX | `make ch10-corpus`、`make ch10-train`、`make ch10-eval` |

`make test` 执行 Python 测试集，部分测试需要专用 MySQL 测试库、Milvus 或 Linux 机制。数据库夹具会重建表，测试库不得指向业务库。前端 `npm test` 与 `npm run test:e2e` 使用受控数据；类型检查和生产构建使用 `npm run build`。受控测试通过不等于真实服务全链路已验收。

## 页面与端口

| 端口 | 用途 |
| --- | --- |
| 5173 | 本地 Vue 开发服务 |
| 8080（可配） | Nginx 生产页面入口 |
| 8000 | FastAPI API，生产使用容器内网 |
| 8101 / 8102 | 物流 / 售后 MCP 服务 |
| 8110 | 主题分类器推理服务 |
| HTTPS（云端） | Langfuse Cloud；3000 仅属于旧自托管配置 |
| 19530 | Milvus |

页面包括 `/`、`/admin`、`/kb`、`/rag-eval`、`/review`、`/observability`、`/topics`、`/topics/questions`，以及 `/acceptance`、`/acceptance/eval`、`/acceptance/data`、`/acceptance/errors`。这些路径由前端提供，业务接口统一使用 `/api`。
