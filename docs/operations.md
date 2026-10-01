# SHAF 后期运维指导

适用：已按[部署手册](../DEPLOY.md)跑通的个人单机实例。**日常使用第 1–4 节；改代码/模型前看第 5 节；需要自动化或长期对外服务时再看第 6 节。** 1Panel 负责容器、文件、日志和计划任务，标准 Nginx 负责页面/API，不依赖 OpenResty。

页面功能验收见[测试指南](testing-guide.md)，源码定位见[架构](architecture.md)。下面明确区分“服务器终端”和“容器终端”；容器名字通常带 `shaf-` 前缀，按服务名识别，不猜编号。数据盘主方案的项目目录是 `/mnt/shaf-data/SHAF`；已部署到其他位置时沿用原路径，避免相对挂载指向空目录。

## 1. 面板查看状态与日志

| 要做什么 | 1Panel 入口 | 关注内容 |
|---|---|---|
| 服务运行情况 | 容器 → 编排 → shaf，或容器列表 | running、healthy、重启次数；running 不等于依赖全部正常 |
| CPU/内存 | 概览/监控、容器资源监控 | 训练/导出时峰值，避免同时跑重任务 |
| 磁盘占用 | 概览/监控；容器 → 概览 | 80GB 系统盘：系统、面板、镜像/构建缓存；100GB 数据盘：数据库、模型、日志、备份 |
| 容器日志 | 目标容器 → 日志 | backend/MCP/classifier 报错；按时间、行数筛选或实时跟随 |
| 进入服务 | 目标容器 → 终端 | backend 选 `/bin/bash`；mysql/frontend 可用 `/bin/sh` |
| 业务文件 | 文件 → `/mnt/shaf-data/SHAF` | 在线查看、编辑、下载日志和报告 |

点击“停止”是停止容器，不是结束日志查看。没有作业运行时才能重启 backend；更改 `.env` 或镜像后需要重新创建，不只重启。**不使用编排“删除并删除文件”清理项目**，原路径接入可能连带删除源码/数据目录。[1Panel 编排](https://1panel.cn/docs/v2/user_manual/containers/compose/)

| 记录 | 项目内位置与用途 |
|---|---|
| 容器标准输出 | 面板日志；启动/访问/MCP/分类器错误，已配置 10MB × 3 轮转 |
| 业务日志 | `log/app.log`：图、知识、反馈、异常；当前文件不自动轮转 |
| 网页作业日志 | `log/acceptance/<作业名>.log`，每次同名作业覆盖，网页显示末 400 行 |
| 报告 | `data/ch04/reports/`、`data/ch09/reports/`、`data/ch10/reports/`，多数同名覆盖 |
| 云调用链 | Langfuse Cloud 项目 Traces |

重要实验重跑前，用“文件”复制/下载旧日志和报告。看报告时间与本次日志，不能把仓库旧结果当作新测试结果。

**重启服务器后先看数据盘挂载。** `findmnt --mountpoint /mnt/shaf-data` 应显示预期数据盘；若 Docker 无法启动，检查挂载、`/etc/fstab` 和 `sudo journalctl -u docker -b --no-pager`。部署手册第 2.3 节的 Docker 启动保护会阻止在未挂载的空目录启动数据库。修复挂载并确认数据仍在后，再启动 Docker；不通过移除保护、格式化或重新建空目录排障。

## 2. 数据库查看

### 2.1 MySQL：容器终端即可

面板 mysql 容器 → 终端 → `/bin/sh`，执行：

```bash
mysql -ushaf -p --default-character-set=utf8mb4 mewhelp
```

输入 `.env` 的 `MYSQL_PASSWORD`，在 `mysql>` 中粘贴需要的 SQL，`exit` 退出：

```sql
SHOW TABLES;
SELECT id,user_id,status,created_at FROM conversations ORDER BY id DESC LIMIT 10;
SELECT role,LEFT(content,180),created_at FROM messages WHERE conversation_id=123 ORDER BY id;
SELECT ticket_no,ticket_type,status,description FROM tickets ORDER BY created_at DESC LIMIT 10;
SELECT vectorize_status,COUNT(*) FROM knowledge_chunks GROUP BY vectorize_status;
SELECT source,COUNT(*) FROM low_confidence_questions GROUP BY source;
SELECT id,normalized_question,occurrence_count,review_status FROM review_queue ORDER BY id DESC LIMIT 10;
SELECT id,tool_name,status FROM tool_audit_logs ORDER BY id DESC LIMIT 20;
SELECT id,triggered_by,dataset_size,metrics,created_at FROM eval_runs ORDER BY id DESC LIMIT 5;
SELECT question_id,labels,classified_at FROM topic_classifications ORDER BY id DESC LIMIT 10;
```

将 `123` 换成页面会话号。这些是查看语句，不修改数据。无 orders 表正常：订单、物流/售后查询是 mock，退款申请复用 tickets 的“退款”类型，不是实际打款。`mewhelp` 保留为兼容数据库名，不随展示名改名。

### 2.2 Milvus 与会话 checkpoint

常规核对优先网页 `/kb`：库存、pending/done、Milvus 条数、四种检索。确需抽查向量记录，在 backend 容器终端执行：

```bash
uv run --no-sync python -c 'from app.kb.milvus_client import get_client,ensure_collection,count; c=get_client(); ensure_collection(c); print(count(c)); print(c.query("knowledge",filter="id >= 0",output_fields=["id","section_path","content_type"],limit=10))'
```

MySQL `knowledge_chunks.id` 与 Milvus 主键对应；数量一致只是初步检查。`data/ch05_checkpoints.sqlite` 保存图线程/中断，不能用 MySQL 替代，也不直接编辑它修复会话。

## 3. Langfuse 调用链复盘

1. 聊天发送易辨识的问题，记录时间、会话号与模型配置。
2. 浏览器进入对应云区域的 Langfuse 项目 → Traces，按时间和输入找请求；上传有短暂延迟。
3. 展开节点/GENERATION，看提示词、工具、输出、错误、耗时和 Token，检查 `intent-tagged` 及 `intent:...` tag。
4. 用 `tool_audit_logs` 判断业务工具是否成功，结合 `log/app.log` 看异常；模型建议不等于数据库已写入。
5. 网页 `/observability` 重跑成本统计、刷新；自定义窗口可在 backend 容器终端执行 `make cost-report DAYS=7`。

成本页显示意图请求/Token 占比，不等于人民币账单；费用看厂商控制台。云区域/key、trace/tag、时间窗口、云配额/保留期都可能导致空报告。脚本使用 Observations v2；不是所有手动脚本都自动完整埋点。[Langfuse SDK](https://langfuse.com/docs/observability/sdk/overview)

## 4. 个人调试中的常用维护

### 4.1 执行入口

| 操作 | 优先入口 |
|---|---|
| 建库、向量化、挖掘、RAG/成本/趋势/校准、训练/评测/导出/归类 | SHAF 对应网页作业按钮 |
| 飞轮标准化归并、黄金样例闸、额外参数 | backend 容器终端 `make flywheel`、`make ch10-golden` 等 |
| 分类器启停 | 已创建容器在 1Panel 启停；首次创建或重新发布用第 4.3 节命令 |
| FAQ/合成会话重新灌种子、知识清库 | 非日常操作，见第 6.3 节；旧 seed-conv/kb-reset 按钮不适用 |

同名网页任务运行中再次点击返回 409，但**不同任务、网页与终端没有全局互斥**。一次只跑一个重任务，训练/导出不并行，不在写入时重启 backend。停止作业不撤销此前数据库写入；重启后内存任务状态归零，磁盘日志保留。

后台不挂 Docker socket。旧 classifier-up/down 管后端进程，不管理独立容器；旧 make dev、基础设施/MCP/自托管 Langfuse 启停不是当前部署入口。

### 4.2 更新知识

面板编辑已有 `data/kb/*.md` 后，网页 `/kb` 依次运行**补丁式重嵌 → 向量化待补块**。终端等价操作（backend 容器）：

```bash
make kb-repatch
make kb-vectorize
```

补丁按小节路径/块序对齐；改标题、切块结构需人工核对。新增小节用 `/kb` 手工录入：kb-build 会跳过已有文档，不能当增量导入器；补丁不自动删除消失的块。

审核写回失败先看日志、新增块与待审状态，不能一概清库重试。置信度校准报告仅推荐参数；要应用，在 `.env` 改 `EVIDENCE_CONFIDENCE_THRESHOLD` 后重新创建 backend。

### 4.3 更新分类模型

先备份 `data/ch10/`，停分类任务（有计划任务也停用），在 1Panel 停止 classifier。网页按**训练 → 评测 → 导出**串行执行；每步通过才继续。发布时在**服务器项目根目录**执行：

```bash
dc() { sudo docker compose -f docker-compose.prod.yml "$@"; }
dc --profile classifier up -d --force-recreate classifier
```

随后跑阈值扫描、单句实验和少量归类。任何阶段失败不发布；恢复上一整套 model、ONNX、tokenizer、threshold 和报告，重新创建服务，不混用新旧产物。旧分类记录不自动重分类。

LoRA 模板秩 64/alpha 128，合并后才导出 ONNX，推理服务不需 Torch。训练仍加载基础模型，16GB 不保证所有任务同时运行。内存不足先降 batch、提高累积步数；有效 batch = batch × 累积。在 backend 容器可用：

```bash
make ch10-train TRAIN_ARGS="--mode lora --batch-size 2 --eval-batch-size 8 --gradient-accumulation-steps 8 --cpu-threads 4"
```

恢复全参用 `.env` `CH10_TRAIN_MODE=full` 后重新创建 backend。LoRA 是否达标以本次评测为准，导出同样需内存余量。

### 4.4 快速排障

| 现象 | 定位顺序 |
|---|---|
| 网页不通 | SSH 隧道/云 IP 放行 → frontend → backend 日志；面板检查端口绑定 |
| 502 | backend 健康 → frontend 日志；后端重建后重启 frontend 刷新上游解析 |
| 文字一次性出现 | 浏览器 SSE → Nginx 缓冲/超时 → 模型流式支持 |
| MySQL 首启失败 | 日志 SQL 位置 → 目录是否为旧数据；不删数据掩盖问题 |
| Milvus unhealthy | etcd/MinIO 日志与凭据 → CPU 指令集/内存/磁盘 |
| 检索无证据 | pending/done → 向量数/维度/中文 BM25 → 嵌入/重排 → 阈值 |
| MCP 工具消失 | 两个 MCP 容器 → `/mcp` 内网地址 → 发现日志/审计 |
| 模型 401/404/429 | key、API 模型名、额度/限流 |
| 分类器重启 | ONNX 三件套/外部权重 → 导出报告 → 容器日志 |
| 分类不增 | healthz → 是否不足 10 条 → 强制小批运行 → classify_run/MySQL |
| 训练退出 137 | 面板内存监控 → 并行重活 → batch；导出也要检查内存 |
| 配置未生效 | `.env` 修改后重新创建，不只 restart |

记录故障时间、提交号、会话/作业号、输入、报错与模型名，去掉 key。400/422 多为输入问题，409 为状态/并发冲突，502/503 常为依赖失败；SSE HTTP 200 仍可能有 `event: error`。

## 5. 修改前备份与版本更新

### 5.1 一致备份：先停，再打包

个人使用不要求每天配置备份平台；在改模型、升级代码或迁移数据前做一次即可。先检查数据盘挂载及剩余空间，项目内归档会额外占用数据盘；暂停计划任务/网页作业，以下在**服务器项目根目录**执行（先定义 `dc`）：

```bash
(
set -e
mountpoint -q /mnt/shaf-data
stamp=$(date +%Y%m%d-%H%M%S)
mkdir -p backups
chmod 700 backups
dc stop frontend backend
dc --profile classifier stop
sudo tar -czf "backups/shaf-$stamp.tar.gz" .env data volumes log
sudo chmod 600 "backups/shaf-$stamp.tar.gz"
sudo tar -tzf "backups/shaf-$stamp.tar.gz" > /dev/null
if [ -d .git ]; then
    git rev-parse HEAD > "backups/version-$stamp.txt"
fi
)
```

任一步失败停止后续步骤，先排错；不要在数据库运行中强行打包。ZIP 上传用户另记源码提交号。归档含配置、MySQL、Milvus/etcd/MinIO、checkpoint、模型/语料和报告；`model-cache/` 可重新下载。

确认成功后启动（服务器终端）：

```bash
dc up -d
# 仅已有可用分类模型时：
dc --profile classifier up -d classifier
```

用 1Panel“文件”下载备份到笔记本；其中含密钥/对话，妥善保管。归档不包含系统配置，新服务器须重新完成数据盘自动挂载和 Docker 启动保护。**网站备份或单独 MySQL 备份不等于完整 SHAF 备份**。面板编排备份的覆盖/恢复行为另需实测；当前保留上述短流程以保证指定数据一起停写、归档。

### 5.2 恢复

优先恢复到新目录/新服务器，保留原环境回退。取得对应提交代码和同版本镜像，用面板上传备份，在**服务尚未启动、目标运行数据为空**时解压到项目根目录；保持数据目录权限/属主。确需命令解压用 `sudo tar -xzf /备份文件绝对路径 -C /目标项目绝对路径`，目标需事先核对。

不要混合新旧 volumes；恢复的 MySQL/MinIO 密码必须与数据一致。按部署流程启动，验收会话、续跑、检索和分类；归档可读不等于恢复已验证。只留同一盘的副本不能应对整机故障。

### 5.3 更新代码/配置

确认无作业、先备份；Git 用户在服务器执行 `git status --short`、记提交号，再 `git pull --ff-only`。ZIP 用户上传新版本源码到临时目录，核对后更新代码文件，**不覆盖旧 `.env`、data、volumes、log 和缓存**。

代码被打包进镜像，**只上传/拉取代码再点“重启”不会应用修复**。只构建、重新创建受影响的服务，不需要整套数据库重启。新终端先在服务器项目根目录定义简写：

```bash
mountpoint -q /mnt/shaf-data || exit 1
cd /mnt/shaf-data/SHAF
dc() { sudo docker compose -f docker-compose.prod.yml "$@"; }
```

**只改前端 Vue / Nginx：**

```bash
dc build frontend
dc up -d --no-deps --force-recreate frontend
```

**只改后端 Python：**

```bash
dc build backend
dc up -d --no-deps --force-recreate backend
dc restart frontend
```

最后一步让 Nginx 重新解析 backend 容器地址，可在 1Panel 重启 frontend 完成。前后端都改时依次执行两组命令，前端会短暂中断。`--no-deps` 保持数据库等依赖服务不动，前提是这些依赖已经正常运行。

改 MCP 时对对应服务执行同样的 build / up 流程；改分类器代码或依赖，用 `dc --profile classifier build classifier` 和 `dc --profile classifier up -d --no-deps --force-recreate classifier`。只改 `.env` 不需构建，但需重新创建受影响服务：后端配置对应 backend，监听地址对应 frontend，分类器线程/批量对应 classifier；后端重新创建后仍重启 frontend。

已有 MySQL 不自动执行新迁移，逐个审核/应用尚未执行的 SQL并记录版本，不能整套重跑。回退用旧提交/源码和对应镜像；数据迁移需兼容回退或一致备份，不能只靠 Git。保留已验收镜像 ID/digest；不使用 `down -v`、清数据目录或全局卷清理排障。

### 5.4 页面或功能出现代码问题

1. 记录页面地址、复现步骤、预期/实际结果、时间和当前提交号；附截图、浏览器 Console/Network 报错及同一时间段的 backend 日志，隐去密钥。先区分配置/依赖故障和代码问题，不通过删数据或重装排障。
2. 开发者可通过已授权的 SSH 直接在服务器源码目录修复、测试并按上节仅更新受影响服务，省去先推送再拉取；修复验证后仍将变更提交并同步 Git，保留回退点。服务器使用只读 Deploy key 时，由开发端回收补丁并推送，不能用只读 key 推送。也可沿用本地修复后拉取的流程。发布后刷新网页（必要时 Ctrl+F5），复测原问题及相关模块，查看容器状态/日志。
3. **正常重启或重新创建容器不会清空当前挂载的数据库、知识库、模型和报告**，前提是保持同一项目路径、挂载配置和宿主数据目录。容器内未挂载的临时文件不受此保护；后端重启会中断当前聊天流和运行中的网页作业，发布前等待作业结束。

若修复涉及数据库结构或已写入的错误数据，由开发者另提供迁移/修复及回退步骤；重启本身不会修复这些数据。新镜像构建成功后再替换容器，构建失败时保留旧实例继续排查。

## 6. 选读：自动化与长期服务维护

### 6.1 1Panel 计划任务

个人测试先手动跑；**应用启动不自动创建定时任务**。需要周期归类/飞轮时：计划任务 → 创建 → Shell 脚本，使用宿主执行；下面两个任务用同一锁，防止彼此并行。

飞轮：页面选择“每 30 分钟”，脚本：

```bash
/usr/bin/flock -n /run/lock/shaf-maintenance.lock /bin/sh -c 'mountpoint -q /mnt/shaf-data && cd /mnt/shaf-data/SHAF && /usr/bin/docker compose -f docker-compose.prod.yml exec -T backend make flywheel'
```

归类：每天 03:00，脚本：

```bash
/usr/bin/flock -n /run/lock/shaf-maintenance.lock /bin/sh -c 'mountpoint -q /mnt/shaf-data && cd /mnt/shaf-data/SHAF && /usr/bin/docker compose -f docker-compose.prod.yml exec -T backend make classify-pool'
```

不勾“在容器中执行”，这些脚本从宿主进入 backend；页面能立即执行、停用和查看报告。先核对服务器时区、目录及 Docker 位置（终端 `timedatectl`、`command -v docker`），做一次立即执行验证。任务需有 Docker 访问权限。[1Panel 计划任务](https://1panel.cn/docs/v2/user_manual/cronjobs/)

锁忙跳过，不排队；这个锁不阻止网页任务，训练/维护期间停用两项。分类默认不足 10 条不跑、最多 500 条，飞轮每轮最多 200 条。已设置系统 cron 时二选一，不重复调度；大型评估仍优先手动。

### 6.2 长期日志与异机备份

容器日志已有轮转；`app.log` 和自定义文件日志没有。可用系统 logrotate 定期归档，当前普通文件句柄适用 `copytruncate`，有极短日志丢失窗口。面板“网站日志切割”不处理 SHAF 的业务日志，不能替代这项维护。

备份可通过 1Panel“面板设置 → 备份账号”连接 COS/OSS/SFTP 等，选择受控存储并设置保留份数。可将第 5.1 节产生的**已完成归档**再由目录备份任务上传，避免在线数据库目录直接打包；备份源不得包含自身输出目录。先实测上传、下载与恢复，再定期运行。[备份账号](https://1panel.cn/docs/v2/user_manual/settings/)

### 6.3 可视化 SQL 客户端与种子维护

喜欢表格浏览时，笔记本用 DBeaver/Navicat 的 SSH 隧道连接。主配置不暴露 MySQL，可在服务器 `/tmp/shaf-db-access.yml` 创建临时覆盖：

```yaml
# 运维临时入口，仅服务器回环可达。
services:
  mysql:
    ports: ["127.0.0.1:13306:3306"]
```

维护时服务器执行 `dc -f /tmp/shaf-db-access.yml up -d mysql`。客户端 SSH 填服务器账号/IP，数据库填 `127.0.0.1:13306`、库 `mewhelp`，使用测试所需账号；建议只读：

```sql
-- 在 mysql root 会话执行，密码改随机值；只授予查看权限。
CREATE USER 'shaf_reader'@'%' IDENTIFIED BY '替换为随机密码';
GRANT SELECT, SHOW VIEW ON mewhelp.* TO 'shaf_reader'@'%';
```

root 会话用 `mysql -uroot -p` 和 `MYSQL_ROOT_PASSWORD`。完成后 `dc up -d mysql` 恢复不映射端口，必要时重新创建 backend 并重启 frontend 验证重连。**此期间不用面板保存编排/环境变量**，避免多文件覆盖与 `.env` 处理混用；不在云防火墙开 3306/13306。

种子仅在空业务库/隔离测试库重灌（服务器终端）：

```bash
dc exec -T mysql sh -c 'MYSQL_PWD="$MYSQL_PASSWORD" mysql -ushaf --default-character-set=utf8mb4 mewhelp' < sql/ch02-seed.sql
# 会删除 seed-% 会话，有外键关联时可能失败，不用于真实业务库：
dc exec -T mysql sh -c 'MYSQL_PWD="$MYSQL_PASSWORD" mysql -ushaf --default-character-set=utf8mb4 mewhelp' < sql/ch03-seed.sql
```

破坏性知识重置由负责人在隔离环境、有备份时处理；会删除飞轮知识，不是日常维护。正式公网上线的 HTTPS/认证清单见[部署手册第 10 节](../DEPLOY.md#10-选读以后正式对外上线)。

## 7. 无面板时的命令备用

在服务器项目根目录定义简写后，可用下面命令替代第 1 节面板操作：

```bash
dc() { sudo docker compose -f docker-compose.prod.yml "$@"; }
dc --profile classifier ps
sudo docker stats --no-stream
sudo docker system df
findmnt --mountpoint /mnt/shaf-data
df -h / /mnt/shaf-data
dc logs --since=30m --tail=100 backend
dc exec backend bash
```

`dc logs -f --tail=100 classifier` 实时跟随，Ctrl+C 只退出查看。已由面板接入的项目仍使用同一文件/项目名，不另建旧 Compose 实例。
