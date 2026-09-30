# SHAF 部署边界与应用容器

本文件是前后端分离后的部署初稿，供后续正式部署设计参考，不代表当前阶段已完成云部署。提供两个应用镜像及组合配置，供 Ubuntu 云端使用。所有服务在云端容器运行；本地只开发、构建和执行不依赖真实基础设施的测试。旧版「本机一键启动」任务书可从 Git 基线查阅，其中初始化、就绪检查和业务验收仍有参考价值。

## 1. 页面与 API 的职责

- Vue 负责十二个页面和交互，生产构建生成 `frontend/dist/`。
- Nginx 提供构建文件，页面 URL 回退到 `index.html`，`/api/` 原样代理到 `backend:8000`。
- FastAPI 只提供业务 API；`/docs`、`/openapi.json` 是后端接口文档，Nginx 默认不对公网代理它们。
- SSE 关闭 Nginx 响应缓冲、缓存及压缩，读取超时为 600 秒。API 错误不会回退成 Vue HTML。
- `/api/health` 仅表示进程存活，不能证明 MySQL、Milvus、MCP 或模型调用正常。

## 2. 已提供的文件

| 文件 | 作用 |
|---|---|
| `frontend/Dockerfile` | Node 构建 Vue，Nginx 镜像只保留静态产物与代理配置 |
| `frontend/nginx.conf` | SPA 路由、哈希资源缓存与 SSE 代理 |
| `Dockerfile.backend` | Python 3.12、uv、make/bash/curl 和 FastAPI 运行环境 |
| `docker-compose.app.yml` | 两个应用容器；合并现有基础设施 Compose 使用 |
| `docker-compose.yml` | 原有 MySQL、Milvus、etcd、MinIO 教学基础设施配置 |
| `docker-compose.langfuse.yml` | 原有独立 Langfuse 观测栈 |

这些是应用容器基础配置，不是已经验收的完整云端部署。当前工作机没有 Docker，镜像构建、Nginx 语法检查及真实容器连通测试尚未执行。

## 3. 云端环境变量

复制 `.env.example` 为 `.env`，填写真实上游配置；不要把密钥写入 Vue 的 `VITE_*` 变量，也不要提交 `.env`。

| 配置 | 在容器中的含义 |
|---|---|
| `DATABASE_URL` | 指向 MySQL 容器，例如 `mysql+asyncmy://业务账号:密码@mysql:3306/mewhelp` |
| `MILVUS_URI` | 组合配置固定为 `http://milvus-standalone:19530` |
| `MCP_LOGISTICS_URL` | 指向物流 MCP 容器的 `/mcp`，例如 `http://mcp-logistics:8101/mcp` |
| `MCP_AFTERSALES_URL` | 指向售后 MCP 容器的 `/mcp`，例如 `http://mcp-aftersales:8102/mcp` |
| `CLASSIFIER_URL` | 指向主题分类器容器，例如 `http://classifier:8110` |
| `LANGFUSE_BASE_URL` | 指向 Langfuse 容器网络内地址，跨 Compose 项目需要共享网络 |
| `CHECKPOINTER_DB_PATH` | 默认 `data/ch05_checkpoints.sqlite`，位于持久化挂载内 |
| `CORS_ORIGINS` | 同域 Nginx 入口保持 `[]`；开发直连后端时填明确来源的 JSON 数组 |
| `FRONTEND_PORT` | Nginx 宿主端口，默认 8080；它是 Compose 配置，不是 FastAPI 设置 |

容器内的 `localhost` 指当前容器自身，不能代替其他服务名。开发机调用云端 API 时，Vite 的 `API_PROXY_TARGET` 则要填开发机能访问的地址。

现有基础设施文件保留原数据库、卷名和教学凭据，避免无意迁移数据。正式云部署时需要配置实际业务账号、密码、内网端口、访问控制和 HTTPS；当前 API 没有登录鉴权，后台页面能够触发写入及计算作业，访问边界须由部署负责人落实。

## 4. Ubuntu 应用容器启动步骤

先完成基础设施以及独立 MCP、分类器容器的配置、建库与知识向量化，再启动应用。服务监听地址需要允许容器网络访问：FastAPI 镜像已使用 `0.0.0.0`；原 MCP/分类器脚本的回环监听在独立容器部署阶段也要配置成容器可访问的监听地址。

```bash
docker compose -f docker-compose.yml -f docker-compose.app.yml config --quiet
docker compose -f docker-compose.yml -f docker-compose.app.yml up -d --build backend frontend
```

使用同一组文件和项目名进行后续管理，避免意外创建另一套数据库卷。MySQL 首次空卷启动时按文件名执行 `sql/`；已有卷不会自动执行新增迁移。不能通过删除数据卷来应用 SQL 更新。

浏览器访问 `http://服务器地址:8080`，后端 8000 不映射公网。若要访问后端 `/docs`，使用受控端口转发或部署负责人添加专用入口。

## 5. 持久化与作业环境

应用组合配置挂载仓库 `data/` 和 `log/`：

- `data/kb/`：知识源文件；后端必须能读取。
- `data/*.sqlite*`：LangGraph checkpoint；中断续跑依赖它。
- `data/ch04/reports/`、`data/ch09/reports/`、`data/ch10/reports/`：页面读取的报告与读图注。
- `data/ch10/`：训练、验证、测试语料与模型/ONNX 产物；不能仅保留报告。
- `log/`：业务日志与作业日志。

MySQL、Milvus、etcd、MinIO 和 Langfuse 各自保留数据卷。备份和恢复必须同时考虑业务库与 checkpoint，避免会话状态和消息历史脱节。

后台作业继续使用原白名单 make 目标及 Linux 进程组停止机制。普通建库、飞轮和评估脚本在后端环境运行；训练/导出需 `ml` 重依赖、足够内存及模型文件，镜像默认不预装这组依赖。作业失败日志应真实呈现，不能用模拟成功替代。

原 `seed-conv` / `kb-reset` 等目标使用 Docker CLI，原 `classifier-up/down` 管理同一执行环境的进程。**它们不会自动管理外部独立服务容器**。本阶段保留这些接口和页面按钮，未引入 Docker socket 或改变服务编排权限；独立容器的启停、数据初始化和训练资源安排需要在后续部署阶段统一对接。应用镜像中的 make 不等于已经具备所有目标的运行前置。

`make dev`、`make mcp-up`、`make classifier-up` 等教程本机多进程启动方式，不作为本项目云端部署流程。

## 6. 验收清单

1. 前端直接访问 `/kb`、`/topics/questions?label=退换货` 等深层路径并刷新，仍显示正确页面。
2. `/api/health` 返回 JSON；不存在的 `/api/...` 返回 API 错误，不能返回 Vue HTML。
3. 聊天逐段显示增量；订单选择、工单确认和取消能续跑；退款表单只在用户确认后提交。
4. MySQL 消息历史与 SQLite 中断状态可跨应用容器重启保留。
5. 知识录入、待审审核、飞轮及报告作业检查真实依赖；分类器不能连通时显示离线。
6. 依赖不可用或报告未生成时，页面保留各模块独立错误/缺产物提示。
7. 训练、写回失败或停止任务的日志与真实执行状态一致。

本地已完成的单元与受控浏览器验证记录见 `docs/frontend-separation-plan.md`。它们验证页面和 API 契约，不能替代上述云端真实链路验收。

## 7. 配置依据

- [Nginx proxy_buffering](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_buffering)：关闭缓冲以即时转发增量。
- [uv Docker 集成](https://docs.astral.sh/uv/guides/integration/docker/)：锁文件依赖同步与独立 Python 运行环境。
- [Vite 静态部署](https://vite.dev/guide/static-deploy.html)：`dist` 是部署产物，开发服务器和 preview 不作为生产入口。
