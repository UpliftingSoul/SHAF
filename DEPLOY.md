# SHAF 个人部署手册

目标：在 Ubuntu 云服务器运行完整项目，通过笔记本浏览器测试、展示各模块。配置按 x86_64、8 核 CPU、16GB 内存、80GB 系统盘 + 100GB 数据盘安排；训练和大型评估串行执行。

本次服务器同时承担开发与调试：核心助手 SSH 接入后，以服务器 `/mnt/shaf-data/SHAF` 为实际工作目录，修改源码/配置、编写文档、测试、Git 提交与推送均在服务器执行；笔记本负责连接和浏览器访问。当前使用 root，`~` 指 `/root`，可省略命令示例中的 `sudo`。root 文件权限与 GitHub 仓库权限独立，第 9 节配置仓库写授权。

**使用标准 Nginx，1Panel 管理容器、文件、日志和计划任务。** 不安装 OpenResty，不使用面板“网站”或“运行环境”启动项目。第 1–8 节是个人使用主线；域名、HTTPS 与正式上线内容放在第 10 节选读。

入口：`docker-compose.prod.yml`。配套：[运维](docs/operations.md)、[网站使用与测试](docs/testing-guide.md)、[Milvus 云端备用规划](docs/milvus-cloud-migration-plan.md)、[架构](docs/architecture.md)。面板步骤依据 1Panel V2 文档和 v2.3.2 源码；尚未在真实 1Panel/Ubuntu 上机验收，部署时逐节确认。

## 1. 访问方式与服务

| 方式 | 笔记本浏览器地址 | 服务器设置 |
|---|---|---|
| **主选：SSH 隧道** | `http://127.0.0.1:8080` | 前端只监听服务器回环地址，不开放网页公网端口 |
| 可选：公网 IP | `http://服务器公网IP:8080` | 前端监听所有 IPv4 地址；云防火墙仅允许本人/展示对象 IP |

SSH 打开的是服务器终端，并不提供图形浏览器。网页在**笔记本浏览器**打开，代码、数据库和计算全部在服务器；两种方式都不需要域名。

链路：浏览器 → **前端 Nginx**（Vue 页面、`/api` 转发）→ **FastAPI** → 数据库/模型/MCP。公网 IP 方式也直接访问这个 Nginx，没有外层代理。

| 服务 | 职责 | 容器内端口 |
|---|---|---|
| frontend | 标准 Nginx：Vue 页面、API 与聊天流式转发 | 80 |
| backend | FastAPI、Agent 工作流、后台作业及 CPU 训练依赖 | 8000 |
| mysql | 会话、工单、知识原文、审核、评估与分类记录 | 3306 |
| milvus-standalone | 1024 维向量、中文 BM25 与混合检索 | 19530 / 9091 |
| etcd / minio | Milvus 元数据、数据/索引对象存储 | 2379 / 9000、9001 |
| mcp-logistics / mcp-aftersales | 物流、售后工具 | 8101 / 8102 |
| classifier | ONNX CPU 分类；有模型后再启动 | 8110 |
| Langfuse Cloud | 云端调用链查看，无本机容器 | HTTPS |

etcd、MinIO 由 Compose 自动连接，无需操作控制台，与图片上传或 MCP 无关。保留它们是本次 Standalone 方案的一部分。服务连接、版本和初始化由项目编排统一定义，**不在应用商店另装一套 MySQL/Milvus**。

## 2. 准备服务器与 1Panel

### 2.1 磁盘与网络

80GB 系统盘放 Ubuntu、1Panel、Docker 镜像和构建缓存；100GB 数据盘挂载到 `/mnt/shaf-data`，项目放 `/mnt/shaf-data/SHAF`，数据库、模型、缓存、日志和备份保存在项目子目录中。无需迁移 Docker 默认存储。

**先确认数据盘是 SSD 或性能合适的云盘。** Milvus 要求 SSD，etcd 对写入延迟敏感；不能只凭“高速硬盘”宣传判断。[Milvus 前置要求](https://milvus.io/docs/v2.6.x/prerequisite-docker.md)

FinalShell **服务器终端**确认一次：

```bash
uname -m
lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINTS,UUID
df -h /
```

架构应为 `x86_64`；80GB/100GB 是盘容量，可用空间更小，显示单位也可能不同。6248R 的型号支持所需 SIMD，仍需用 `lscpu` 核对虚拟机实际暴露的指令集。

**数据盘一次性初始化（在部署容器前完成）：**

可先完成第 2.2 节的面板与工具安装，再回本节初始化；在第 2.3 节保护配置完成前，不启动项目容器。

1. 云控制台确认新增 100GB 数据盘已关联本实例；结合容量、挂载点和盘标识，识别它与挂载 `/` 的系统盘。设备可能是 `/dev/vdb` 或 NVMe 名称，不能照抄别人的盘符。
2. 若数据盘已经有文件系统或数据，跳过格式化，核对已有内容后挂载。只有确认是全新空盘才初始化为 ext4；按服务商 Ubuntu 初始化指引执行，通用 Linux 步骤可参考[云盘初始化说明](https://help.aliyun.com/zh/ecs/user-guide/initialize-a-data-disk-whose-size-does-not-exceed-2-tib-on-a-linux-instance)。**格式化会清空目标盘，不能操作系统盘。** 不熟悉盘符时，由远程助手核对后执行。
3. 格式化后用 `lsblk -f` 获取目标文件系统的 UUID，创建挂载目录并备份配置：

```bash
sudo mkdir -p /mnt/shaf-data
sudo cp -a /etc/fstab /etc/fstab.shaf-before
lsblk -f
```

用 1Panel“文件”或 VSCode 远程编辑 `/etc/fstab`（需管理员权限），**保留已有行**，新增下面一行。面板尚未安装时可用 `sudo nano /etc/fstab`；把 `实际文件系统UUID` 替换为上一步结果，不能原样保存：

```text
UUID=实际文件系统UUID /mnt/shaf-data ext4 defaults,nofail,x-systemd.device-timeout=30s 0 2
```

`nofail` 使盘异常时系统仍能启动供 SSH 排查；第 2.3 节会要求 Docker 等待并检查数据盘，不能省略。若云厂商已经配置自动挂载，修改原条目即可，不添加重复挂载。

4. 服务器终端执行并检查：

```bash
sudo systemctl daemon-reload
sudo mount -a
findmnt --mountpoint /mnt/shaf-data
df -h / /mnt/shaf-data
```

`mount -a` 不应报错，`findmnt` 必须显示预期数据盘/文件系统。只有同名目录、不是真实挂载时不能继续。**首次部署在云控制台重启一次服务器**，重新 SSH 登录并重复挂载检查，验证开机自动挂载后再启动项目。

云安全组/轻量服务器防火墙只开放 SSH 和面板端口给自己的公网 IP（单个 IPv4 写成 `你的IP/32`）。SSH 主方案不用开 8080；数据库、后端、Milvus、MCP、分类器端口均不放行。出站需能访问模型 API、Langfuse、GitHub、镜像及依赖下载源。

本次服务商要求备案后开放公网 80/443，**不影响 SSH 隧道主方案**；Nginx 的容器内部 80 无需公网放行。公网 IP + 8080 仅在服务商明确允许后使用，不以换端口绕过其规定。调用云 API 的出站 HTTPS 与开放公网入站 443 是不同方向。

### 2.2 面板与基础工具

已有 1Panel/Docker 时跳过安装。新服务器按 [1Panel V2 官方在线安装](https://1panel.cn/docs/v2/installation/online_installation/)执行，社区版即可；当前安装说明通过 `PANEL_INSTALL_DOCKER=y` 显式启用 Docker 安装。已有 Docker 则复用，不覆盖配置。

在笔记本浏览器访问安装结果给出的“IP:面板端口/安全入口”。主要使用 **容器、文件、计划任务**，资源在概览/监控查看。菜单名称可能随补丁调整，按功能定位即可。

服务器终端补齐小工具，确认 Compose 可用：

```bash
sudo apt update
sudo apt install -y git openssl util-linux nano
sudo docker version
sudo docker compose version
```

宿主不安装 Python、uv、Node、conda 或 MySQL，镜像包含所需环境。两块盘分别看占用：系统盘仍承担 Docker 镜像/构建缓存，数据盘承担项目运行数据。备份下载到笔记本后按需移除旧副本；磁盘接近满时先排查占用，不删除数据库目录或正在使用的卷。

### 2.3 Docker 启动前检查数据盘

本次新服务器使用普通 systemd Docker 服务。在项目容器尚未启动时，服务器终端执行下面的一次性配置；`[Unit]` 要求启动前挂载数据盘，并确认路径真的是挂载点：

```bash
sudo mkdir -p /etc/systemd/system/docker.service.d
sudo tee /etc/systemd/system/docker.service.d/shaf-storage.conf > /dev/null <<'EOF'
[Unit]
RequiresMountsFor=/mnt/shaf-data
AssertPathIsMountPoint=/mnt/shaf-data
EOF
sudo systemctl daemon-reload
sudo systemctl restart docker
sudo systemctl status docker --no-pager
```

应为 `active (running)`；可用 `sudo systemctl cat docker` 确认上述配置已加载。数据盘未挂载时先排查磁盘，**不删这项保护或在空目录启动项目**。此配置作用于整台机器的 Docker；如果已有其他容器，先安排停机再重启 Docker。日常修复只更新应用容器，不重复这一步。[systemd 挂载依赖](https://www.freedesktop.org/software/systemd/man/systemd.unit.html#RequiresMountsFor=)

## 3. 源码与配置就位

### 3.1 取得源码：任选一种

首次部署先在服务器终端创建目录，允许当前 SSH 用户写入：

```bash
mountpoint -q /mnt/shaf-data && sudo mkdir -p /mnt/shaf-data/SHAF
sudo chown "$(id -un):$(id -gn)" /mnt/shaf-data/SHAF
```

首条命令失败时先解决挂载，不继续操作。若已经按其他路径部署，继续沿用原目录，并统一替换本手册中的 `/mnt/shaf-data/SHAF`；第 2.3 节的保护适用于数据盘主方案。**不要仅换路径重新启动**：相对挂载会指向新的数据目录，看起来像空库；迁移应按运维手册备份/恢复。

**可视化方式：** 笔记本登录 GitHub 私有仓库下载 ZIP；在 1Panel“文件”上传、解压，整理为 `/mnt/shaf-data/SHAF`。确认此目录直接包含 `app/`、`frontend/`、`sql/`、`docker-compose.prod.yml`，不多套一层目录。FinalShell 上传也可，不传 `.venv/`、`node_modules/`、密钥或历史 Git 备份；记录下载的提交号。

**Git 方式（本次主选）：** 按第 9 节添加仅对 SHAF 仓库生效、允许写入的 Deploy key 后，在服务器终端执行：

```bash
git clone git@github.com:UpliftingSoul/SHAF.git /mnt/shaf-data/SHAF
```

目标目录应不存在或为空，当前用户需有写权限。新终端先回项目根目录。知识源和评估材料在仓库中；分类语料、dataset、model、ONNX 不在 Git 中。已有产物上传完整 `data/ch10/`，没有则按第 7 节生成；已有 reports 不代表新服务器通过验收。

### 3.2 编辑 `.env`

1Panel“文件”打开项目目录，复制 `.env.deploy.example` 为 `.env`，在线编辑并保存；VSCode 远程编辑也可。确保文件名是 `.env`，不是 `.env.txt`。

以下命令在服务器终端运行**三次**，三个不同密码分别填 `MYSQL_ROOT_PASSWORD`、`MYSQL_PASSWORD`、`MINIO_ROOT_PASSWORD`：

```bash
openssl rand -hex 24
```

十六进制密码避免数据库 URL 转义问题，其余必填内容：

| 配置 | 填写内容 |
|---|---|
| `CHAT_BASE_URL` / `CHAT_MODEL` | `https://api.deepseek.com/v1` / `deepseek-flash` |
| `CHAT_API_KEY` | DeepSeek 官方 key |
| `CHAT_THINKING` | `disabled`，其他推理专属项留空 |
| `MODEL_CONTEXT_WINDOW` | 模板 `1000000`；换模型时填实际窗口 |
| `EMBED_BASE_URL` / `EMBED_MODEL` | `https://api.siliconflow.cn/v1` / `BAAI/bge-m3` |
| `RERANK_BASE_URL` / `RERANK_MODEL` | `https://api.siliconflow.cn/v1` / `BAAI/bge-reranker-v2-m3` |
| `EMBED_API_KEY` / `RERANK_API_KEY` | 对应平台 key，同账号可相同 |
| `FRONTEND_BIND` / `FRONTEND_PORT` | 主选 `127.0.0.1` / `8080` |

API 模型名与产品展示名不同，实际可用模型以[官方接口](https://api-docs.deepseek.com/api/create-chat-completion/)为准。关闭思考适配工具链；不需要中转。`INTENT_*`、`SUMMARY_*` 留空复用聊天模型。换 Qwen 时改官方兼容地址、模型/key，清空 DeepSeek/MiniMax 专属参数，再测试流式工具调用。首次保留 1024 维嵌入模型。

服务地址由 Compose 注入，不自行填宿主公网 IP；同域 `CORS_ORIGINS=[]`。CPU 模板采用 LoRA 秩 64、alpha 128、batch 4、累积 4、线程 4，先保留此起点。

### 3.3 Langfuse Cloud

浏览器创建云端项目，在项目设置 API Keys 创建密钥，填 `.env`：

```dotenv
LANGFUSE_PUBLIC_KEY=pk-lf-你的公钥
LANGFUSE_SECRET_KEY=sk-lf-你的密钥
LANGFUSE_BASE_URL=https://cloud.langfuse.com
```

EU 如上；US 用 `https://us.cloud.langfuse.com`，JP 用 `https://jp.cloud.langfuse.com`，匹配项目区域。SDK 已接入，无需改代码；三项全空可暂时关闭。不启动旧自托管 Langfuse 配置。[官方接入](https://langfuse.com/docs/observability/get-started)

## 4. 一次性构建，然后接入面板

### 4.1 构建项目镜像

以下在 **FinalShell 服务器终端**执行，构建期间保持连接。`dc` 是本手册 Compose 简写，新终端重新定义：

```bash
mountpoint -q /mnt/shaf-data || exit 1
cd /mnt/shaf-data/SHAF
mkdir -p log volumes model-cache backups
chmod 600 .env
dc() { sudo docker compose -f docker-compose.prod.yml "$@"; }
dc config --quiet
dc build mcp-logistics mcp-aftersales backend frontend
dc --profile classifier build classifier
```

`config --quiet` 无错误才继续，`.env` 不再含 `REPLACE_WITH_...`。CPU 依赖下载较慢，失败先看网络和具体报错，无需宿主装 uv/Torch。

保留这段命令的原因：当前 1Panel 导入先尝试拉镜像，而 SHAF 镜像需本机构建；先构建避免导入时拉取 `shaf-*:local` 失败，也不受面板构建任务时限影响。classifier 此时只构建、不启动，不要求已有模型。

### 4.2 1Panel 原路径接入

1. **容器 → 编排 → 创建编排**，选择“路径选择”。
2. 选择 `/mnt/shaf-data/SHAF/docker-compose.prod.yml`，项目名保持 `shaf`。
3. 检查环境变量栏已载入同目录 `.env` 的完整内容；保存会写回文件，**不能留空或删 key**。
4. 不勾“强制拉取镜像”，提交后看任务日志，等待完成。
5. `shaf` 详情中应有八个基础服务运行，有健康检查的服务最终 healthy；classifier 先不启动。

原路径保留构建上下文、`.env` 和相对数据目录；不要单独粘贴 YAML 到另一目录。失败时在同一编排排查，不另建第二套项目。[1Panel 编排说明](https://1panel.cn/docs/v2/user_manual/containers/compose/)

### 4.3 确认 MySQL 首启

MySQL 仅在**空数据目录首启**按文件名执行 `sql/`，自动建表、写 FAQ/合成会话。不执行旧 `make seed` / `make seed-conv`。

1Panel 找到 mysql 容器 → 终端，选择 `/bin/sh` 连接，执行：

```bash
mysql -ushaf -p --default-character-set=utf8mb4 mewhelp
```

输入 `.env` 的 `MYSQL_PASSWORD`（不回显），再执行：

```sql
SHOW TABLES;
SELECT COUNT(*) AS faq_count FROM faq;
```

应含 `knowledge_chunks`、`review_queue`、`topic_classifications` 等业务表，FAQ 非空；`exit` 退出。已有目录不重放 SQL，首启出错先看 mysql 日志，不删数据重试。

## 5. 打开网页与初始化知识

### 5.1 笔记本建立 SSH 隧道

FinalShell SSH 隧道使用**本地端口转发**：本地 `8080` → 服务器目标 `127.0.0.1:8080`。也可在**笔记本终端**运行：

```bash
ssh -N -L 8080:127.0.0.1:8080 ubuntu@服务器公网IP
```

替换账号/IP并保持连接；浏览器打开 `http://127.0.0.1:8080`。本机端口占用时左侧改 18080，浏览器也改 18080，右侧仍是服务器 8080。隧道断开只影响访问，容器继续运行。

### 5.2 上游验证与知识入库

1Panel → backend 容器终端，选择 `/bin/bash`，依次执行：

```bash
uv run --no-sync python scripts/smoke_embed.py
uv run --no-sync python scripts/smoke_toolcall.py
```

嵌入应为 1024 维；工具验证应返回 `add` 并打印 `GO`。`NO-GO` 可能仍以 0 退出，需看正文。

网页 `/kb` 依次执行**材料预览 → 离线建库 → 向量化待补块**，每步结束再继续。确认六份材料已处理、pending 为 0、MySQL done 与 Milvus 条数一致。已有文档块整体跳过，更新材料用补丁入口，不清库。

用“邮费是多少”“猫砂盆 Pro 型号”测试 vector、bm25、hybrid、hybrid_rerank，检查正文与来源；再聊天问“订单 1001 的物流到哪了”，检查工具轨迹。模型措辞和合理排名可变化。

## 6. 标准 Nginx 的实践要点

`frontend/nginx.conf` 构建时自动放进前端镜像，无需再装 Nginx：

| 配置 | 作用 |
|---|---|
| `listen 80` | 容器内部监听，Compose 映射为服务器 8080 |
| `root` / `index` | 提供 Vue 构建后的静态页面 |
| `location /` + `try_files` | 前端路由深层路径刷新仍返回 `index.html` |
| `/api/` + `proxy_pass http://backend:8000` | 内网转发，保留 `/api` 前缀 |
| `proxy_buffering off` / `proxy_cache off` | SSE 增量及时到达 |
| `proxy_read_timeout 600s` | 留出模型处理等待时间 |

frontend 容器终端执行 `nginx -t` 检查配置。修改源码中的配置后需**重新构建并创建 frontend**，重启不会更新镜像内文件。具体操作见运维手册。[Nginx 入门](https://nginx.org/en/docs/beginners_guide.html)

## 7. 分类器就绪

### 7.1 已有模型

上传 `data/ch10/onnx/`，至少含 `model.onnx`、`tokenizer.json`、`threshold.json`；外部权重文件一并上传。重新评测/验收还需完整 `model/`、`dataset/` 和 reports，不能只上传报告。

服务器项目根目录，重新定义 `dc` 后执行：

```bash
dc --profile classifier up -d classifier
```

面板无本项目 profile 专用开关，显式命令最直接。随后可用 1Panel 启停**已创建的 classifier 容器**、看日志。其容器终端验证：

```bash
python -c 'import urllib.request; print(urllib.request.urlopen("http://127.0.0.1:8110/healthz").read().decode())'
```

### 7.2 无模型

1. backend 容器终端执行 `make ch10-golden`，确认黄金样例闸通过。
2. 网页 `/acceptance/data` 运行语料流水线；面板文件打开 `data/ch10/sample_review.md` 抽审，纠错改对应 `corpus_labeled.jsonl`。只改抽审 Markdown 不影响训练。
3. 同页依次运行数据集划分与增强、训练；到 `/acceptance/eval` 执行测试集评测，通过后回数据页 ONNX 导出。
4. 按 7.1 启动分类器，再在评测页跑阈值扫描，执行旁路归类；少量数据用“不足一批强跑”。

基础模型首次从 Hugging Face 下载到 `model-cache/`。网络受限可上传完整模型目录，在 backend 终端使用 `make ch10-train TRAIN_ARGS="--base-model /app/data/ch10/base-model"`。LoRA 自动合并后沿用评测/ONNX，无额外推理平台。

**不使用旧网页“拉起/停止分类器服务”按钮**，它们控制后端进程，不是独立容器。再次训练/导出前停 classifier，更新和回退见运维手册。

## 8. 最短验收与可选公网 IP

### 8.1 首次验收

1. 首页、`/kb` 正常，深层路径刷新正常；`http://127.0.0.1:8080/api/health` 返回 JSON。
2. 聊天逐段输出、工具/引用可见，工单确认/取消、退款选单/表单可操作。
3. 四路检索有合理证据，知识条数和向量化状态一致。
4. Langfuse Cloud 出现本次 trace，成本页重跑后有本次统计。
5. 分类器上线后单句实验、批量归类和主题页通过。
6. 无作业运行时在面板重启 backend，历史保留；中断续跑按测试指南验证。

health 只证明进程存活；未生成新报告时显示缺产物正常。全部模块验收见[测试指南](docs/testing-guide.md)。

### 8.2 可选：公网 IP 访问

个人测试可限制来源 IP，不要求域名、HTTPS 或额外代理：

1. 云安全组/防火墙允许 **TCP 8080，来源仅自己公网 IPv4/32**，展示时增加对方 IP。检查没有另一条允许所有来源的 8080/全端口规则，否则限制无效。
2. 面板编辑 `.env`：`FRONTEND_BIND=0.0.0.0`，端口仍为 8080。
3. 服务器项目根目录执行 `dc up -d --force-recreate frontend`，不是 restart。
4. 浏览器打开 `http://服务器公网IP:8080`，重验聊天流式输出和写操作。

应用没有登录鉴权，此方式仅限本人/获准展示者，**不开放给任意公网访问者**。Docker 发布端口可能绕过宿主防火墙，来源限制须在云控制台生效，不能只依赖面板主机防火墙。[Docker 端口说明](https://docs.docker.com/engine/network/port-publishing/)

恢复 SSH 专用：改回 `FRONTEND_BIND=127.0.0.1`、重新创建 frontend、删除云侧 8080 放行。切换网址会改变浏览器本地会话选择，服务器数据并未丢失。

## 9. GitHub 授权与远程工作目录

本次需要在服务器直接 clone/pull/commit/push，使用仓库专用的**可写 Deploy key**。它只授权 SHAF，不需要配置整个 GitHub 账号的访问密钥。SSH 登录服务器的密钥与此 GitHub 密钥是两套凭据，root 身份不会自动获得 GitHub 推送权限。

服务器终端生成独立密钥；已有同名文件则复用，不重复执行生成命令或覆盖原文件：

```bash
mkdir -p ~/.ssh
chmod 700 ~/.ssh
ssh-keygen -t ed25519 -f ~/.ssh/shaf_deploy -C shaf-server
cat ~/.ssh/shaf_deploy.pub
```

GitHub 仓库 **Settings → Deploy keys → Add deploy key** 粘贴公钥，**勾选 Allow write access** 后保存。[官方说明](https://docs.github.com/en/authentication/connecting-to-github-with-ssh/managing-deploy-keys#deploy-keys)

已经添加为只读时，在该仓库删除对应的只读授权条目，再用原公钥添加并勾写权限即可；**不删除或重新生成服务器上的密钥文件**，不需要重做部署或重启容器。私钥保留服务器，不上传 Git 或发到聊天。

服务器 `~/.ssh/config` 添加：

```sshconfig
Host github.com
    HostName github.com
    User git
    IdentityFile ~/.ssh/shaf_deploy
    IdentitiesOnly yes
```

已有 github.com 设置则合并；首次连接核对[官方主机指纹](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints)，之后按第 3 节克隆。

克隆后，在服务器项目目录设置提交身份；替换下面两项为自己的提交名和 GitHub 已验证邮箱或账号实际 noreply 邮箱，仅配置当前仓库：

```bash
cd /mnt/shaf-data/SHAF
git config user.name "你的Git提交名"
git config user.email "你的Git提交邮箱"
git remote -v
git status --short
```

远端应为 `git@github.com:UpliftingSoul/SHAF.git`。SSH 连接成功、clone 成功只证明能读取；GitHub 页面应明确显示该 key 有写权限，首次实际推送再确认可写。后续执行方式见[运维手册第 5 节](docs/operations.md#5-修改前备份与版本更新)。

## 10. 选读：以后正式对外上线

以下是后续上线的准备清单，**现在个人部署全部跳过**。继续使用标准 Nginx，无需改用 OpenResty。

| 事项 | 后续操作 |
|---|---|
| 域名 | DNS A 记录指向服务器；Nginx `server_name` 设置域名 |
| HTTPS | 申请可信证书，证书/私钥从宿主只读挂载；Nginx 配置 `listen 443 ssl`、`ssl_certificate`、`ssl_certificate_key`，Compose/云防火墙同步开放 443 |
| 证书续期 | 采用 ACME 客户端并安排续期任务，续期后 `nginx -t`、reload；不把私钥写进 Git/镜像 |
| 访问认证 | 当前应用无账号体系；限定展示先用 Nginx Basic Auth 保护**整个站点及 `/api`**，必须结合 HTTPS。真正面向客户需补充应用身份与权限设计 |
| HTTP 入口 | 80 仅作 HTTPS 跳转/证书验证；不保留无保护的 8080 旁路 |
| 备份/维护 | 运维手册保留一致备份、异机副本、恢复、版本更新、SQL 迁移及日志归档；上线前实际演练 |

当前配置只监听容器 80，**不能只开放 443 就获得 HTTPS**。需先扩展 Nginx 配置、挂载证书，再调整 Compose 端口映射；届时按实际域名/证书来源实施并验收。Basic Auth 的 server 层核心配置如下，密码文件由 `htpasswd` 生成并只读挂载，位置与配置一致：

```nginx
auth_basic "SHAF access";
auth_basic_user_file /etc/nginx/shaf.htpasswd;
```

公网后重验页面刷新、SSE、确认续跑及写操作。若将来增加外层代理，该层同样合并 `proxy_buffering off; proxy_cache off; proxy_read_timeout 600s; proxy_send_timeout 600s;`，不覆盖整份配置。

参考：[Nginx HTTPS](https://nginx.org/en/docs/http/configuring_https_servers.html)、[Basic Auth](https://nginx.org/en/docs/http/ngx_http_auth_basic_module.html)。高并发/多机方案等实际需要时再设计，不是当前任务。
