# SHAF 后期运维指导

面向维护人员：已按 [部署手册](../DEPLOY.md)跑通服务后，使用本手册查看数据、追踪问题、安排作业与备份更新。功能验收见[测试指南](testing-guide.md)，文件定位见[架构手册](architecture.md)。命令在 Ubuntu 项目根目录执行。

## 1. 日常状态与日志

```bash
cd /mnt/shaf-data/SHAF
dc() { sudo docker compose -f docker-compose.prod.yml "$@"; }
dc --profile classifier ps
sudo docker stats --no-stream
df -h / /mnt/shaf-data
sudo docker system df
dc logs --since=30m --tail=100 backend
dc logs -f --tail=100 classifier
```

`Ctrl+C` 结束日志跟随，不停止容器。查看任一容器将最后的服务名替换即可。宝塔 Docker 页面可辅助查看容器状态、日志和资源，操作时识别 `shaf` 项目，不启动旧 Compose。

| 记录 | 位置与用途 |
|---|---|
| 容器标准输出 | `dc logs`：启动、访问日志、MCP、分类器错误；Compose 配置 10MB × 3 轮转 |
| 业务日志 | `log/app.log`：图运行、知识/反馈、异常堆栈；当前 FileHandler 不自动轮转 |
| 网页作业日志 | `log/acceptance/<作业名>.log`；每次同名作业覆盖，网页只显示末 400 行 |
| 手动/定时脚本日志 | 重定向指定文件，例如 `log/cron-flywheel.log`；网页日志不包含宿主命令 |
| 评估报告 | `data/ch04/reports/`、`data/ch09/reports/`、`data/ch10/reports/`；多数同名结果覆盖 |
| 云调用链 | Langfuse 项目中的 traces；用于模型调用和图步骤复盘 |

重要实验先复制日志、报告，再重跑。业务日志和 cron 日志需定期归档，可用宝塔计划任务或系统 logrotate；当前 `app.log` 为普通文件句柄，使用 `copytruncate` 方式轮转有极短日志丢失窗口。不要假定容器日志限额也限制了宿主文件日志。

## 2. 数据库查看

### 2.1 MySQL：优先只读核对

```bash
dc exec mysql sh -c 'MYSQL_PWD="$MYSQL_PASSWORD" mysql -ushaf --default-character-set=utf8mb4 mewhelp'
```

进入 `mysql>` 后执行 SQL，`exit` 退出：

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

将 `123` 换成页面会话编号。没有 orders 表正常：订单、物流和售后查询为稳定 mock。退款申请复用 tickets，`ticket_type='退款'`；不表示实际打款。MySQL `mewhelp` 是兼容数据库名，不需要随展示名改名。

### 2.2 可视化客户端（可选）

笔记本可用已有 Navicat / DBeaver 等 MySQL 客户端，通过 SSH 隧道连接。默认 MySQL 不映射宿主端口；需要时在服务器创建**临时** `/tmp/shaf-db-access.yml`：

```yaml
# 运维临时入口，仅服务器回环可达。
services:
  mysql:
    ports: ["127.0.0.1:13306:3306"]
```

维护窗口执行 `dc -f /tmp/shaf-db-access.yml up -d mysql`，会重新创建 MySQL 容器。客户端配置 SSH 为服务器账号/IP，数据库地址 `127.0.0.1:13306`，库名 `mewhelp`。建议创建只读账号：

```sql
-- 在 root 会话中执行，将示例密码替换为随机值。
CREATE USER 'shaf_reader'@'%' IDENTIFIED BY '替换为随机密码';
GRANT SELECT, SHOW VIEW ON mewhelp.* TO 'shaf_reader'@'%';
```

root 会话用前述进入命令将 `MYSQL_PASSWORD` 改 `MYSQL_ROOT_PASSWORD`、`-ushaf` 改 `-uroot`。读取完执行 `dc up -d mysql` 恢复主配置的无端口映射状态，必要时重建后端和前端验证重连。不要在安全组开放 13306 或 3306。

### 2.3 Milvus 与 checkpoint

普通知识核对优先用 `/kb`：库存、pending/done、Milvus 在线与条数、检索自测。需要详细行时：

```bash
dc exec backend uv run --no-sync python -c 'from app.kb.milvus_client import get_client,ensure_collection,count; c=get_client(); ensure_collection(c); print(count(c)); print(c.query("knowledge",filter="id >= 0",output_fields=["id","section_path","content_type"],limit=10))'
```

MySQL `knowledge_chunks.id` 与 Milvus 主键一一对应；条数相等是初步一致性检查，不能证明每条正文完全相同。`data/ch05_checkpoints.sqlite` 保存 LangGraph 线程/中断，不用 MySQL 表替代，也不直接编辑 SQLite 修复会话。

## 3. Langfuse 调用链复盘

1. 聊天发送一个易辨识的问题，记录时间、页面会话号、模型配置。
2. 打开所选区域的 Langfuse 项目，进入 Traces，按时间窗口找到对应输入；后台上传存在短暂延迟。
3. 展开图节点与 GENERATION，查看实际提示词、工具调用、输出、错误、耗时、输入/输出 Token；检查 `intent-tagged` 事件及 `intent:...` tag。
4. 结合 `tool_audit_logs` 判断工具是否成功，结合 `log/app.log` 看本地异常。不要把 trace 中的模型建议当成已写数据库的业务结果。
5. 有数据后执行 `dc exec backend make cost-report DAYS=7`，刷新 `/observability`。

成本页统计带意图的请求及 Token，占比不等于人民币账单；实际费用看厂商账单，缓存/不同模型价格也不同。成本脚本分页读取 Observations v2，不下载对话正文。网页默认统计 7 天；无 tag、区域/key 错误、上传失败、云配额/保留期限制均可能导致空报告。[Langfuse SDK](https://langfuse.com/docs/observability/sdk/overview)

云 trace 可能含对话与提示词，使用测试数据并按项目成员权限管理访问。当前不同链路的 trace 覆盖以 SDK 实际记录为准，不假定每个手动脚本都自动完整埋点。

## 4. 作业、知识与分类器维护

### 4.1 网页作业的边界

作业同名重入返回 409，但**不同作业没有全局互斥**；网页和宿主命令也不共享锁。不要并行训练/导出/评估，不在写入期间重启后端。停止作业会终止进程组，不自动撤回此前已提交的数据库记录或删除半成品。后端重启后内存作业状态归零，磁盘日志仍在。

| 操作 | 当前容器部署入口 |
|---|---|
| 建库、向量化、挖掘、RAG/成本/趋势/校准、训练/评测/导出、分类 | 网页相应作业，或 `dc exec backend make <目标>` |
| 飞轮标准化归并 | `dc exec backend make flywheel`；网页无独立飞轮运行按钮 |
| 分类器容器启停 | `dc --profile classifier up -d classifier` / `dc stop classifier` |
| FAQ/会话重新灌种子 | 负责人宿主执行 SQL；旧网页 seed-conv 依赖 Docker CLI，不适用 |
| 破坏性知识重置 | 负责人在测试环境处理；旧网页 kb-reset 不适用且会删除飞轮知识 |

后端不挂 Docker socket。旧 classifier-up/down 只管后端进程，可能产生多余服务或误导成功提示；不使用这两个按钮。Makefile 的 dev、MCP/基础设施/自托管 Langfuse 启停也不用于生产。

若确实需重灌种子，仅在隔离测试库/空业务库执行：

```bash
dc exec -T mysql sh -c 'MYSQL_PWD="$MYSQL_PASSWORD" mysql -ushaf --default-character-set=utf8mb4 mewhelp' < sql/ch02-seed.sql
# 合成会话脚本会删除 seed-% 会话；其已有外键关联时可能失败，不用于有真实数据的库。
dc exec -T mysql sh -c 'MYSQL_PWD="$MYSQL_PASSWORD" mysql -ushaf --default-character-set=utf8mb4 mewhelp' < sql/ch03-seed.sql
```

### 4.2 知识更新与质量维护

修改已有 `data/kb/*.md` 后：

```bash
dc exec backend make kb-repatch
dc exec backend make kb-vectorize
```

补丁按小节路径和同节块序对齐，更新正文并置 pending；改标题、改变切块结构或新增小节时需人工核对。**新增小节用 `/kb` 手工录入**：kb-build 已有文档块会整体跳过，不能把它当增量导入器。补丁不自动删除已消失的块；删除/重组知识需另做有备份的迁移。

待审知识经人工核准写回，向量失败应保留审核待处理状态；重试前看错误和新增块，部分失败可能存在远端残留，不能一概重置知识库。校准后报告只是推荐，手动在 `.env` 设置 `EVIDENCE_CONFIDENCE_THRESHOLD=...`，重新创建后端才生效。

### 4.3 分类器模型更新

先备份整个 `data/ch10/`，维护窗口暂停分类 cron 和网页分类任务，停止分类器，再训练/评测/导出：

```bash
dc stop classifier
dc exec backend make ch10-train
dc exec backend make ch10-eval
dc exec backend make ch10-export
dc --profile classifier up -d --force-recreate classifier
dc exec backend make ch10-threshold-scan
```

任何一步失败不发布；回退时恢复上一套 model、ONNX、tokenizer、threshold、相关报告再重建分类器。不能把新模型阈值和旧 ONNX 混用。旧 topic_classifications 不会因更新模型自动重分类；需要重分类时先另行规划数据迁移。

CPU 训练模板选 LoRA 秩 64/alpha 128。要恢复全参，把 `.env` `CH10_TRAIN_MODE=full` 后重建后端；临时参数可用：

```bash
dc exec backend make ch10-train TRAIN_ARGS="--mode lora --batch-size 2 --eval-batch-size 8 --gradient-accumulation-steps 8 --cpu-threads 4"
```

LoRA adapter 留在 `model/adapter/`，部署推理使用合并模型导出的 ONNX。LoRA 不保证与全参同分；8 核 16GB 是可尝试 CPU 训练的起点，先观察峰值/时长。内存不足先减少 batch、避开其他重活；有效 batch 为 batch × 累积步数。导出也会加载多个模型，需资源余量。

### 4.4 定时任务：需要显式安装

源码提供批处理脚本，**应用启动不会自动创建 cron**。先手动跑通，再按需 `sudo crontab -e` 添加以下示例，使用同一个锁串行运行 cron；日志目录先存在：

```cron
*/30 * * * * /usr/bin/flock -n /run/lock/shaf-maintenance.lock /bin/sh -c 'cd /mnt/shaf-data/SHAF && /usr/bin/docker compose -f docker-compose.prod.yml exec -T backend make flywheel' >> /mnt/shaf-data/SHAF/log/cron-flywheel.log 2>&1
0 3 * * * /usr/bin/flock -n /run/lock/shaf-maintenance.lock /bin/sh -c 'cd /mnt/shaf-data/SHAF && /usr/bin/docker compose -f docker-compose.prod.yml exec -T backend make classify-pool' >> /mnt/shaf-data/SHAF/log/cron-classify.log 2>&1
```

时间按服务器时区，先 `timedatectl` 确认。锁被占用时本轮跳过，不是排队。分类默认不足 10 条不跑，每轮最多 500 条；少量测试用 `FORCE=1`。飞轮每轮最多 200 条，失败留待重试。大型评估建议手动，确需定时可低频执行 `make eval-flywheel TRIGGER=定时`。宝塔“计划任务 → Shell 脚本”可设置相同命令和周期，与 cron 二选一，避免重复调度。维护/训练期间暂停计划任务；此锁不会阻止网页作业。

## 5. 备份、恢复与更新

### 5.1 一致性备份（个人项目推荐停机方式）

暂停计划任务，确认作业停止，先关入口/后端再停全部服务：

```bash
stamp=$(date +%Y%m%d-%H%M%S)
mkdir -p backups
dc stop frontend backend
dc --profile classifier stop
sudo tar -czf "backups/shaf-$stamp.tar.gz" .env data volumes log
sudo chmod 600 "backups/shaf-$stamp.tar.gz"
git rev-parse HEAD > "backups/version-$stamp.txt"
sudo tar -tzf "backups/shaf-$stamp.tar.gz" > /dev/null
dc up -d mysql etcd minio milvus-standalone mcp-logistics mcp-aftersales backend frontend
# 已经具备分类器模型时：
dc --profile classifier up -d classifier
```

归档成功再启动。备份包括 MySQL、etcd、MinIO、Milvus、checkpoint、模型/语料、报告和配置；`model-cache/` 可重新下载，不要求备份。归档含密钥和对话，保管好，复制到另一台机器/受控存储。只把副本留同一数据盘不能应对整机故障。

恢复优先在新目录/新服务器演练：挂好盘，取对应提交代码，用同版本镜像，将归档解到该项目目录后启动上述服务。恢复目录的持久数据必须为空，别把新旧 volumes 混合；保留原环境作为回退。不同服务器 `.env` 可按实际入口调整，但 MySQL/MinIO 初始密码必须与恢复的数据一致。重新验收会话、续跑、知识双写和分类。备份条目可读不等于已经验证可恢复。

### 5.2 代码与配置更新

确认没有运行作业并暂停调度，先做备份、记录当前提交：

```bash
git status --short
git rev-parse HEAD
git pull --ff-only
dc build mcp-logistics mcp-aftersales backend frontend
dc up -d --force-recreate mcp-logistics mcp-aftersales backend frontend
dc --profile classifier up -d --build classifier
```

锁文件和镜像 tag 变化先看变更说明。已有 MySQL 数据目录不自动执行新 SQL；逐个审核并应用尚未执行的迁移，记录日期/版本，不能整套重跑。需要回退时检出已记录提交、重新构建部署；数据迁移不能仅靠 Git 回退，需兼容迁移或一致备份恢复。

改 `.env` 后使用 `dc up -d --force-recreate backend`，不是 restart；分类器线程/批量改动也重新创建 classifier。后端地址重建后可 `dc restart frontend` 刷新 Nginx 上游 DNS。不要在没有备份的情况下用 `down -v`、清数据目录或全局 prune volumes 排障。

## 6. 故障定位顺序

| 问题 | 定位顺序 |
|---|---|
| 网页/API 不通 | SSH/域名入口 → 外层代理 → frontend → backend 日志 |
| 文字一次性出现 | 浏览器 Network 的 SSE → 内外 Nginx 缓冲/超时 → 模型流式支持 |
| 数据库错误 | mysql healthy → 连接账号/库名 → SHOW TABLES/缺迁移 → 后端日志 |
| 无证据/大量拒答 | pending/done → Milvus 数量/中文 BM25 → 嵌入/重排 → 当前阈值/语义自检 |
| MCP 工具消失 | MCP 容器日志/监听 → 网络 URL `/mcp` → 动态发现日志 → 审计表 |
| 成本页空 | 云区域和项目 → key → 新 trace 和 intent tag → 成本脚本日志/窗口 |
| 分类已启动但数据不增 | healthz → 未分类数量是否不足 10 → FORCE=1 → classify_run → MySQL |
| 内存或磁盘耗尽 | docker stats、df、system df → 正在跑的重任务 → 日志/模型/缓存占用 |

提交故障记录应包含时间、提交号、会话/作业号、操作步骤、错误日志和模型名，去除密钥。400/422 多为输入错误，409 为状态/并发冲突，502/503 常是依赖失败；SSE HTTP 200 仍可能包含 `event: error`，要看帧和日志。
