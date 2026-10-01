# SHAF 部署手册

适用：单台 x86_64 Ubuntu 云服务器，8 核 CPU、16GB 内存、80GB 数据盘，低并发开发验收。项目和全部服务运行在服务器；笔记本使用 FinalShell / VSCode 远程操作。以下命令均在**服务器终端**执行，另有标注的除外。

入口为 `docker-compose.prod.yml`。配套：[运维指导](docs/operations.md)、[网站使用与测试](docs/testing-guide.md)、[Milvus 云端备用规划](docs/milvus-cloud-migration-plan.md)、[架构手册](docs/architecture.md)。已核对源码与配置，尚未在真实 Ubuntu 构建镜像和验收外部服务；部署时逐节完成验收。

## 1. 服务组成

| 服务 | 职责 | 容器内端口 |
|---|---|---|
| frontend | Nginx 提供 Vue 页面，代理 `/api/`、SSE | 80 |
| backend | FastAPI、Agent 图、网页作业；含 CPU 训练依赖 | 8000 |
| mysql | 会话、工单、知识原文、审核、评估、分类记录 | 3306 |
| milvus-standalone | 1024 维向量 + 中文 BM25 混合检索 | 19530 / 9091 |
| etcd | Milvus 元数据与协调 | 2379 |
| minio | Milvus 数据、索引对象存储 | 9000 / 9001 |
| mcp-logistics / mcp-aftersales | 物流、售后工具 | 8101 / 8102 |
| classifier | ONNX CPU 旁路分类；有模型后启动 | 8110 |
| Langfuse Cloud | 云端 trace，无本机容器 | HTTPS |

MinIO、etcd 是这套 Standalone 配置的配套服务，由 Compose 自动连接；与网页图片上传、MCP 无关，无需单独操作控制台。云端 Milvus 可省去这三个本机容器，但需按备用规划加鉴权和迁移索引。

16GB 是整机资源，不保证所有任务同时满负荷运行。训练、导出、大型评估串行执行并观察内存；LoRA 仍需完整基础模型计算。购机优先 x86_64、SSD，核对 CPU 指令集及[Milvus 官方前置要求](https://milvus.io/docs/v2.6.x/prerequisite-docker.md)。

## 2. 准备服务器

### 2.1 数据盘与网络

在云控制台完成数据盘挂载和开机自动挂载。本手册假定挂载点 `/mnt/shaf-data`；实际路径不同则统一替换。宝塔可辅助查看磁盘，不格式化已有数据盘。

```bash
uname -m
free -h
lsblk -f
findmnt /mnt/shaf-data
df -h / /mnt/shaf-data
```

验收：`x86_64`、数据盘实际挂载、容量符合预期。`findmnt` 无输出时先挂载，不能只创建同名目录继续部署。

首次只开放 SSH 给自己的 IP，用隧道验收；公网入口之后再开放 80/443。不开放数据库、Milvus、MCP、分类器、后端端口。允许出站访问模型 API、Langfuse、GitHub、镜像仓库和依赖下载源。

### 2.2 Docker 与基础工具

先检查 `sudo docker version`、`sudo docker compose version`。宝塔已安装且正常则复用，只补 git/openssl 等工具。未安装时，支持的 Ubuntu 系统按下列顺序设置官方 apt 源并安装；与[官方安装说明](https://docs.docker.com/engine/install/ubuntu/)核对，遇到旧版包冲突先处理，不盲目卸载宝塔现有服务：

```bash
sudo apt update
sudo apt install -y ca-certificates curl git openssl util-linux
sudo install -d -m 0755 /etc/apt/keyrings
sudo curl -fsSL -o /etc/apt/keyrings/docker.asc https://download.docker.com/linux/ubuntu/gpg
sudo chmod 0644 /etc/apt/keyrings/docker.asc
source /etc/os-release
printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu %s stable\n' \
  "$(dpkg --print-architecture)" "${UBUNTU_CODENAME:-$VERSION_CODENAME}" | sudo tee /etc/apt/sources.list.d/shaf-docker.list
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo docker run --rm hello-world
sudo docker compose version
```

宿主无需安装 Python、uv、Node、conda 或 MySQL，镜像包含所需环境。以下采用 `sudo docker`。

镜像和构建缓存仍可能占系统盘。如需移到数据盘，在首次拉镜像前按[官方存储配置](https://docs.docker.com/engine/daemon/#configure-the-data-directory-location)设置 Docker `data-root`，例如 `/mnt/shaf-data/docker`。Docker Engine 29+ 新安装的 containerd 镜像存储需另外设置 `/etc/containerd/config.toml` 的 `root`；只改 `data-root` 不会移动这部分。合并已有宝塔配置，不覆盖原文件；有现存容器时不能按空机方式直接迁目录。

## 3. 取得代码与产物

### 3.1 私有仓库

推荐服务器只读部署密钥，不把个人 token 写进克隆 URL：

```bash
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -C shaf-server
cat ~/.ssh/id_ed25519.pub
```

已有密钥不要覆盖。浏览器进入 SHAF 仓库 **Settings → Deploy keys → Add deploy key**，粘贴公钥，不勾写权限。首次连接核对 [GitHub 主机指纹](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints)。

```bash
sudo mkdir -p /mnt/shaf-data/SHAF
sudo chown "$(id -un):$(id -gn)" /mnt/shaf-data/SHAF
git clone git@github.com:UpliftingSoul/SHAF.git /mnt/shaf-data/SHAF
cd /mnt/shaf-data/SHAF
git rev-parse --short HEAD
mkdir -p log volumes model-cache backups
```

也可 FinalShell 上传完整源码；不上传笔记本 `.venv/`、`node_modules/`、历史 Git 备份及密钥。新终端先回项目根目录。

### 3.2 Git 未保存的运行文件

知识源和评估材料在仓库中；分类语料、切分数据、训练模型、ONNX 被忽略。有可用产物则上传完整 `data/ch10/` 目录结构，否则按第 7 节生成。仓库已有 reports **不能证明新服务器通过验收**，以本次日志、时间及配置为准。

## 4. 填写配置

```bash
cp .env.deploy.example .env
chmod 600 .env
openssl rand -hex 24
```

随机密码命令运行三次，分别填 `MYSQL_ROOT_PASSWORD`、`MYSQL_PASSWORD`、`MINIO_ROOT_PASSWORD`；十六进制密码避免数据库 URL 特殊字符转义。用 VSCode 远程编辑 `.env`，每行一项，不保留 `REPLACE_WITH_...`。

### 4.1 模型 API

| 配置 | 主方案 |
|---|---|
| `CHAT_BASE_URL` / `CHAT_MODEL` | `https://api.deepseek.com/v1` / `deepseek-flash` |
| `CHAT_API_KEY` | DeepSeek 官方 key |
| `CHAT_THINKING` | `disabled`，其他推理专属项留空 |
| `MODEL_CONTEXT_WINDOW` | `1000000`；换模型时填实际窗口 |
| `EMBED_BASE_URL` / `EMBED_MODEL` | `https://api.siliconflow.cn/v1` / `BAAI/bge-m3` |
| `RERANK_BASE_URL` / `RERANK_MODEL` | `https://api.siliconflow.cn/v1` / `BAAI/bge-reranker-v2-m3` |
| `EMBED_API_KEY` / `RERANK_API_KEY` | 对应账号 key；同账号可相同 |

API 名与展示版本名分开，当前为 `deepseek-flash`，见[官方接口](https://api-docs.deepseek.com/api/create-chat-completion/)。主方案关闭思考，以适配现有强制工具调用和消息续传链路。代码支持官方地址，无需中转。意图/摘要 `INTENT_*`、`SUMMARY_*` 留空则复用聊天模型。

改 Qwen 时换官方 OpenAI 兼容地址、模型和密钥，清空 DeepSeek/MiniMax 专属的 `CHAT_THINKING`、`CHAT_REASONING_EFFORT`、`CHAT_REASONING_SPLIT`，再验收结构化输出、流式工具调用、退款和工单。嵌入维度固定 1024，首次不随意换模型；计费、额度以各账号控制台为准。

### 4.2 Langfuse Cloud

浏览器注册云端账号、创建组织和项目，在项目设置 API Keys 中创建密钥：

```dotenv
LANGFUSE_PUBLIC_KEY=pk-lf-你的公钥
LANGFUSE_SECRET_KEY=sk-lf-你的密钥
LANGFUSE_BASE_URL=https://cloud.langfuse.com
```

EU 如上；US 为 `https://us.cloud.langfuse.com`；JP 为 `https://jp.cloud.langfuse.com`，必须对应项目区域。SDK 已接入，成本脚本已适配 Observations v2。三项全空可暂时关闭。不启动旧 `docker-compose.langfuse.yml`，不运行 `make langfuse-up`。[官方接入](https://langfuse.com/docs/observability/get-started)

### 4.3 服务与训练

数据库/Milvus/MCP/分类器地址由 Compose 提供，容器内 localhost 是当前容器自身。同域 `CORS_ORIGINS=[]`。模板用可选 LoRA：秩 64、alpha 128、batch 4、累积 4、线程 4；源码默认仍 full。训练资源调整见运维手册。

## 5. 构建并启动存储

项目根目录定义简写，**新终端重新定义**：

```bash
dc() { sudo docker compose -f docker-compose.prod.yml "$@"; }
dc config --quiet
dc pull mysql etcd minio milvus-standalone
dc build mcp-logistics mcp-aftersales backend frontend
dc up -d mysql etcd minio milvus-standalone
dc ps
dc images
```

验收：四项最终 healthy。首次 CPU 镜像下载训练依赖需时间。持续 unhealthy 查看 `dc logs --tail=100 服务名`，不删目录反复重试；拉取失败先查官方源和出站网络。

记录本次提交号、镜像 ID/可用的仓库 digest。应用基镜像的 Python 3.12、Node 22、Nginx stable 以及 MySQL 8.4 标签可随补丁更新；需要精确复现时保留已验收镜像或将其 digest 固定，不只记录 tag。

MySQL 仅在**空数据目录首启**按文件名执行 `sql/`，含建表、迁移、FAQ 和合成会话种子。已有库不会自动执行新迁移，也不能重放全部非幂等 DDL。

```bash
dc exec mysql sh -c 'MYSQL_PWD="$MYSQL_PASSWORD" mysql -ushaf --default-character-set=utf8mb4 mewhelp -e "SHOW TABLES; SELECT COUNT(*) AS faq_count FROM faq;"'
```

验收：业务表完整，含 `knowledge_chunks`、`review_queue`、`topic_classifications`，FAQ 非空。不再执行旧 `make seed` / `make seed-conv`。

Milvus 固定 v2.6.22，PyMilvus 按锁文件；官方说明新 SDK 协议向后兼容，但推荐同系列。仍以实际中文 BM25、向量与混合检索通过为准，不自动升级。[兼容说明](https://milvus.io/api-reference/pymilvus/v3.0.x/About.md)

## 6. 知识初始化与应用验收

```bash
dc run --rm --no-deps backend uv run --no-sync python scripts/smoke_embed.py
dc run --rm --no-deps backend uv run --no-sync python scripts/smoke_toolcall.py
dc run --rm --no-deps backend make kb-preview
dc run --rm --no-deps backend make kb-build
dc run --rm --no-deps backend make kb-vectorize
dc up -d mcp-logistics mcp-aftersales backend frontend
dc ps
curl -fsS http://127.0.0.1:8080/api/health
```

验收：嵌入 1024 维；工具冒烟实际返回 `add`、输出 `GO`；知识向量非空；应用 healthy，health 返回 JSON。工具冒烟打印 `NO-GO` 也可能以 0 退出，需检查正文。建库处理六份材料，已有文档块整体跳过；更新知识用补丁或网页录入，不清库。

**笔记本终端**建立 SSH 隧道，替换账号/IP：

```bash
ssh -N -L 8080:127.0.0.1:8080 ubuntu@服务器公网IP
```

保持窗口，浏览器打开 `http://127.0.0.1:8080`。FinalShell 可用可视化 SSH 隧道配置；本机端口占用时把第一处 8080 改 18080，浏览器同步改。

`/kb` 用“邮费是多少”“猫砂盆 Pro 型号”测试 vector、bm25、hybrid、hybrid_rerank，检查正文和来源；聊天问“订单 1001 的物流到哪了”，检查订单、物流工具轨迹。允许模型措辞/合理排名变化。详细验收见测试指南。

## 7. 分类器就绪

### 7.1 已有模型

`data/ch10/onnx/` 至少有 `model.onnx`、`tokenizer.json`、`threshold.json`；存在外部权重时一并上传。重新评测、验收页还需完整 `model/`（config、权重、tokenizer、阈值）、`dataset/`、reports。仅有报告无法启动服务。

```bash
dc --profile classifier up -d --build classifier
dc --profile classifier ps
dc exec classifier python -c 'import urllib.request; print(urllib.request.urlopen("http://127.0.0.1:8110/healthz").read().decode())'
```

### 7.2 无模型：生成与训练

依次运行，每条结束再继续。预标/增强会消耗模型配额：

```bash
dc exec backend make ch10-golden
dc exec backend make ch10-corpus
```

黄金样例闸通过后再生成语料。VSCode 打开服务器 `data/ch10/sample_review.md` 抽审；纠错改对应 `corpus_labeled.jsonl` 的标签/文本，**只改抽审 Markdown 不影响训练**。确认后：

```bash
dc exec backend make ch10-dataset
dc exec backend make ch10-train
dc exec backend make ch10-eval
dc exec backend make ch10-export
dc --profile classifier up -d --build classifier
dc exec backend make ch10-threshold-scan
dc exec backend make classify-pool FORCE=1
```

基础模型首次从 Hugging Face 下载到挂载的 `model-cache/`。网络受限可上传完整模型目录，训练传 `TRAIN_ARGS="--base-model /app/data/ch10/base-model"`。LoRA 自动合并成标准模型再走 ONNX；无需额外推理平台。评测红线未达标则修数据/配置，不改考题凑分。

网页可训练、评测和导出；独立分类器启停使用 Compose，**不用旧网页“拉起/停止分类器服务”按钮**。再次导出前停分类器，导出验证通过后重建/启动；更新步骤见运维手册。

## 8. 首次交付清单与公网入口

1. `/`、`/kb`、主题问题深层路径刷新正常；未知 API 返回错误而非网页。
2. 聊天增量、工具、引用可见；退款选单/表单、工单确认/取消正常。
3. `/kb` pending 为 0，MySQL done 与 Milvus 数一致，四路检索合理。
4. MCP 物流、保修、退货查询可用；分类服务、单句实验、旁路写库通过。
5. Langfuse 出现本次 trace 和意图标注；`dc exec backend make cost-report` 有本次数据。
6. 无作业运行时 `dc restart backend`，历史保留；中断续跑按测试指南验收。

health 只证明进程存活；缺少新报告显示缺产物是正常状态，不代表部署失败。

个人调试可持续用 SSH 隧道。需要公网时，在宝塔“网站”创建独立反向代理站点，目标 `http://127.0.0.1:8080`，配置域名、HTTPS 证书及**整个站点的访问认证**。应用没有登录鉴权，浏览器用户标识不是认证身份。

外层代理也关闭缓冲/缓存，允许 POST SSE，超时至少 600 秒。宝塔配置中查看并合并 `proxy_buffering off; proxy_cache off; proxy_read_timeout 600s; proxy_send_timeout 600s;`，不覆盖面板整份配置。不要只保护后台页面却开放 `/api`；公网后重验 SSE 和写操作。

## 9. 启动排障

| 现象 | 先查 |
|---|---|
| 下载失败 | DNS、出站、官方源；CPU Torch 和 Hugging Face 各需可达 |
| MySQL 首启失败 | 日志中 SQL 失败位置、目录是否空；不通过删数据掩盖问题 |
| Milvus unhealthy | etcd/MinIO、凭据、CPU 指令集、内存、磁盘及日志 |
| 网页 502 | backend 健康和 frontend 日志；后端重建后刷新 frontend 代理解析 |
| 模型 401/404/429 | key、API 模型名、额度和限流 |
| 检索无结果 | pending/done、向量数、维度、中文 BM25、重排逐层查 |
| 分类器重启 | ONNX 三件套、外部权重、导出报告、classifier 日志 |
| 训练被杀/退出 137 | 内存、并发重活；降低 batch 并调整梯度累积 |
| `.env` 修改不生效 | restart 不重载环境，需重新创建容器 |

日常排障见[运维手册](docs/operations.md)。不混用旧 `docker-compose.yml`、`docker-compose.app.yml`、`make dev` 启动入口。
