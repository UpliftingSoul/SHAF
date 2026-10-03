# SHAF: Stable Hybrid Agentic Framework for E-Commerce Support

SHAF 是面向电商售后场景的智能客服框架，以确定性 Workflow 与 ReAct Agent 的混合架构为核心，在开放式对话中保留可控的业务执行顺序。系统覆盖知识问答、订单与售后动作、长对话管理、质量评估和知识持续维护，采用独立 Vue 3 前端与 FastAPI 后端。

## 核心设计与亮点

- **可控的 Workflow + Agent 混合编排**：LangGraph 将九类意图分流至五类处理路径。知识问答强制检索，退款先验证订单归属再查政策；Agent 在既定路径内组合工具，减少遗漏必要步骤和越界承诺。
- **多路混合检索与证据约束**：Milvus 承载语义向量与中文 BM25 召回，经 RRF 融合、多子句召回和重排形成可引用证据。生成前结合四信号置信度与语义自检，证据不足时拒答并沉淀知识缺口。
- **分层上下文与长对话管理**：近期原文、较早消息压缩和异步分段摘要共同管理上下文，配合工具结果裁剪、模型窗口预算及稳定提示词前缀，控制长对话中的信息保留与调用开销。
- **统一工具执行与人工确认**：内置工具与 MCP 动态发现接入同一注册表和执行引擎，统一参数校验、权限、超时、重试与审计。订单选择和工单确认支持 interrupt/resume，退款由用户明确提交表单后写入。
- **可观测与人工审核数据飞轮**：Langfuse 记录调用链和意图成本；低置信度问题、用户反馈与离线分析进入知识维护链路，经标准化、查重和人工审核回写。MySQL 保存知识原文，Milvus 保存索引，双写失败保留待补写状态。
- **独立主题分类与轻量推理**：17 类多标签主题体系统一数据治理、预标注、训练与评估，通过阈值扫描定标和 ONNX 推理，为知识缺口排序提供旁路主题分布，不干扰在线客服意图路由。
- **评估驱动的维护后台**：十二个 Vue 页面覆盖客服交互、知识录入、审核、RAG、成本与分类验收。报告、作业日志和编造个案台账保留各自口径，明确区分缺值、失败与真实零值。

当前订单与物流查询保留 mock 数据，人工转接为模拟交互；接入真实业务系统时可通过工具和 MCP 扩展。项目包含检索、路由、上下文及分类评估材料，具体指标应结合对应数据集、报告和运行配置解读。

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
