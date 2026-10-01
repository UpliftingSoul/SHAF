# Milvus 云端迁移备用规划

状态：**备用设计，尚未执行**。主部署继续使用 Milvus Standalone。仅当资源、运维或连接稳定性需要时，再按本规划改造；当前代码不能只改一个 URI 就完整接入需要 token 的云集群。

## 1. 目标与保持不变的部分

选用兼容 Milvus API 的 Zilliz Cloud Serving 集群，业务 MySQL、Vue、FastAPI、MCP、Langfuse Cloud 和分类器仍在原方案中。移除本机 Milvus、etcd、MinIO 后减少维护对象；网络时延、区域、额度、费用和凭据则交由云侧管理。

不改 `knowledge_chunks` 表、不改 chunk 主键或邻接关系，不重写切块、嵌入、重排、RRF、置信度/语义自检。MySQL 继续是原文权威源，Milvus 主键继续使用 MySQL id。只是更换向量存储位置与连接鉴权，迁移已有索引。

## 2. 先确认云集群能力

创建集群前在云控制台/文档核对所选区域和规格：

- 支持当前集合所需的 1024 维 FLOAT_VECTOR、SPARSE_FLOAT_VECTOR、BM25 Function。
- 支持 `analyzer_params={"type":"chinese"}`、COSINE/AUTOINDEX、BM25 稀疏倒排索引。
- 支持 `hybrid_search` + RRFRanker、标量 category 过滤、upsert、flush、load、count 查询。
- 服务器可访问 HTTPS endpoint，网络延迟/出站与 IP 白名单可用；配额容纳真实知识数。

不能因为“兼容 Milvus”就默认某一规格支持所有功能。先用临时集合做小样本验证，再迁移正式知识。依据：[集群连接](https://docs.zilliz.com/docs/connect-to-serving-cluster)、[全文检索](https://docs.zilliz.com/docs/full-text-search)、[混合检索](https://docs.zilliz.com/docs/hybrid-search)。

## 3. 最小代码变更清单

| 文件 | 拟改动 |
|---|---|
| `app/config.py` | 加 `milvus_token: str = ""`；如所选云项目使用非默认 database，再加对应可选设置 |
| `app/kb/milvus_client.py` | 默认客户端传 URI 和 token；保留显式测试 URI 分支、单例与专用线程机制 |
| `.env.example`、`.env.deploy.example` | 说明 `MILVUS_URI`、`MILVUS_TOKEN`，不包含真实云 key |
| 生产 Compose | backend 不再强制覆盖本机 URI，删除对 Milvus 的 depends_on；移除本机三服务或另建云版独立入口 |
| 冒烟/迁移辅助脚本 | 使用统一配置连接；新增独立临时集合验证和全量索引重建工具 |
| 测试及手册 | token 传参/未配置回归、schema 与四种检索、审核双写、云端备份约定 |

默认客户端示意（真正实施时写测试，不在本轮提前修改）：

```python
# 默认连接增加鉴权，Standalone 空 token 仍兼容。
kwargs = {"uri": settings.milvus_uri}
if settings.milvus_token:
    kwargs["token"] = settings.milvus_token
_CLIENT = MilvusClient(**kwargs)
```

`get_client(uri=...)` 的测试分支保持现有语义，不自动把生产 token 发给任意测试 URI。所有默认连接保持在专用线程中惰性创建，不能为切云改成跨线程共享。

### 3.1 各业务模块影响

| 链路 | 当前文件 | 判断 |
|---|---|---|
| 集合构造/连接 | `app/kb/milvus_client.py` | 必须补鉴权；schema/index 能力通过云端测试后原则上不改 |
| 文档/手工录入 | `scripts/build_kb.py`、`app/api/kb.py`、`app/kb/documents.py` | 切块、去重、原文入库不改 |
| 向量双写 | `app/kb/dualwrite.py`、`scripts/vectorize_kb.py` | 保留 pending→嵌入→upsert→done；通过默认客户端自然切换 |
| 知识补丁 | `scripts/kb_repatch.py` | 原文补丁规则不改，已有 done 数据不会自动迁移 |
| 历史挖掘/飞轮核准 | `app/kb/mining.py`、`app/api/kb.py`、`app/api/review.py`、`app/core/flywheel.py` | 核准和 rollback 语义不改，实际云写回必须回归 |
| 检索 | `app/core/retrieval.py`、`app/kb/milvus_client.py` | dense/BM25/hybrid/RRF/重排与 ID 回查不改 |
| 邻块与证据闸 | `app/core/confidence.py`、`app/core/selfcheck.py`、数据库仓储 | 无 schema 变更，验证引用与邻块回查正确 |
| chunk 数据库 | `app/db/models.py`、`app/db/repository.py`、`sql/ch03-ddl.sql` | 不迁移 MySQL，不重新分配 chunk id；仅迁移期间重置向量状态 |

正式实施前再次阅读这些模块的当前版本，避免把备用规划当永远不变的源码承诺。

## 4. 数据迁移步骤

### 4.1 停写与一致备份

暂停 cron/宝塔调度、停止网页/脚本写入，做运维手册的一致备份，记录代码提交和 `.env`。保留原 Standalone 数据、原 embedding 模型与原索引；不删除原卷/目录。

### 4.2 先验证临时集合

配置云 endpoint/token，使用独立临时集合复现当前 schema。插入少量真实中文块验证四种检索、category 过滤、同 id upsert、flush 后可见、count 和引用回查；测试通过再建立空的正式 `knowledge`。如果云已有同名集合，先确认归属和 schema，不能直接覆盖或 drop。

旧 `scripts/smoke_milvus_bm25.py` 硬编码 localhost 且会删除 `smoke_bm25` 集合，不直接拿来做正式云验证；实施时改为配置化、显式临时集合名并核对当前 SDK。

### 4.3 全量重建索引（保留全部原文）

仅改 URI 后运行 kb-vectorize **不够**：已有 done 块会被跳过，云集合仍空。小规模知识库优先从完整 MySQL 原文重新嵌入，包含文档、历史挖掘和飞轮核准的块。

建议实施时新增专用重建脚本：明确目标 endpoint、停写状态、块总数，分批读取所有原文、沿用相同 embedding、保留 id，全部验证后记录完成状态。最小替代方案是在备份、停写且新目标确认正确后，把所有知识块 `vectorize_status` 改 pending、`vector_id` 置 NULL，再对云执行现有向量化；不删除任何原文/邻接信息。

重建会再次调用 embedding，有费用和耗时；失败则保留 pending，可按 id upsert 重试。当前双写先标 done 后最终 flush；若末尾 flush 失败，不能仅看 pending=0，需要显式检查云可见性、补 flush/load 和逐 ID 对账。数量相等仍不代替正文/检索校验。

**禁止 `kb-reset` 或 `kb-build` 替代迁移**：前者删除知识与待审记录，后者只覆盖文件材料或跳过已有块，不能保留完整飞轮知识。

### 4.4 切换验收

确保所有默认客户端使用云配置并重建 backend，完成：

1. MySQL 原文总数、done 数与云 count 对齐，并抽样比对 id、正文、来源；新集合不含多余历史行。
2. 四策略检索、中文型号词、语义问法和 category 过滤正确。
3. 手工录入、pending 补向量、历史 QA 采纳、飞轮审核通过与故障回滚符合原语义。
4. 聊天引用、邻块回查、置信度闸和 RAG 回归无明显退化；结果变化按质量评估判断。
5. 重启后仍可 load/检索，实际账号具备所需权限。

通过后恢复入口与调度，再停止本机 Milvus/etcd/MinIO。保留原数据备份与一段观察期，不以停服务等同于可以删数据。

## 5. 部署侧简化

云版独立 Compose 保留 frontend、backend、mysql、两个 MCP、classifier；删除 Milvus/etcd/MinIO 服务及相关 depends_on、卷挂载。backend 的 URI/token 来自 `.env`，不被主方案本机地址覆盖。云端密钥仅后端可见，前端不设置 VITE 密钥。

首次迁移建议独立文件 `docker-compose.cloud.yml`，保持项目名、MySQL 和 data 挂载与主方案一致；勿混用不同文件启动出两套数据库。并行调试临时环境则使用不同项目名、网络、持久数据，避免抢占。

运维不再备份本机 etcd/MinIO/Milvus；仍备份 MySQL、checkpoint、语料/模型和配置，并启用所选云服务实际支持的备份/恢复能力。不能因为向量可重建就忽略飞轮原文备份。

## 6. 回退

迁移过程中保持停写，失败时恢复原配置、原代码和一致数据库备份，重新启用原三服务和 backend/frontend，核对原索引与原文。若切云后已经产生新知识，不能仅把 URI 改回旧库：需要把新增/变更块同步回原索引，或按明确的数据截断时间恢复备份；先另行制定增量回退方案。

本规划不承诺“零代码云迁移”，也不涉及 MySQL 架构重构。实际实施的关键工作是小范围鉴权接入、云功能兼容验证和完整数据对账。
