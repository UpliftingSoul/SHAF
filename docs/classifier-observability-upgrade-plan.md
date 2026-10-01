# 分类器与云端观测升级规划

## 1. 范围与兼容目标

- Milvus 主方案采用 Standalone；部署手册及云端迁移备用方案在后续阶段编写。
- 本次接入 Langfuse Cloud 的新版查询接口，保留 Trace、意图 Token 统计和页面报告契约。
- 分类器增加可选 LoRA，保留全参数微调默认行为、17 类多标签、验证集阈值扫描、评测与 ONNX 导出链路。
- ONNX 本次保留；不修改分类标签、数据库结构、Agent 路由或知识检索。

## 2. 实现方案

### 云端观测

沿用现有 Langfuse SDK 的地址及密钥配置。成本脚本使用 Observations v2 游标分页读取事件，以 trace ID 关联意图及模型用量，避免旧 Metrics v1 停用和 v2 高基数分组限制。只累加模型生成事件的直接 Token 用量，按 trace 去重计请求，不重复统计父级汇总。

### 可选 LoRA

训练入口支持 full/lora 两种模式；默认 full，LoRA 默认秩 64、缩放 128，作用于 BERT attention 的 query/value，同时训练并保存分类头及 pooler（从掩码语言模型载入时可能新初始化）。训练参数可通过命令参数或环境配置覆盖。

LoRA 仅保存可训练参数的最优内存快照，减少额外内存；校准后保存独立适配器，并合并成标准模型写入原模型目录。现有评测和导出程序继续读取标准模型，不承担适配器加载逻辑。较高秩增加适配能力，但不能保证与全参数微调分数相同，仍以留出测试集结果验收。

### 推理与资源

ONNX 推理按可配置的小批次执行，保持输入顺序、标签阈值、兜底和响应格式。CPU 训练与常驻推理分开安装依赖；不在日常推理服务中引入 Torch 或 PEFT。

## 3. 验证与边界

1. 验证云端分页、重复事件去重、跨页意图关联、Token 口径及失效游标处理；不使用真实账号制造测试 Trace。
2. 用本地随机初始化的小型 BERT 跑真实 full/LoRA 训练、最优权重恢复、阈值校准、标准模型重载、ONNX 导出及预测一致性。
3. 验证分批推理与原整体推理结果一致，以及空输入处理。
4. 运行相关纯逻辑及受控接口回归；无法连接的 MySQL/Milvus、Linux Docker 和真实云端账号联调明确列为未验证项。
5. 本次提交仅包含本轮相关升级及配套验证，先前未完成的部署草稿保留在工作区。

## 4. 使用入口

CPU 训练环境：`uv sync --locked --group ml --extra cpu`。原全参数命令保持兼容；服务器执行 `make ch10-train ML_UV_ARGS="--extra cpu"`。

可选 LoRA：`make ch10-train ML_UV_ARGS="--extra cpu" TRAIN_ARGS="--mode lora --batch-size 4 --eval-batch-size 16 --gradient-accumulation-steps 4 --cpu-threads 4"`。有效训练批量为 4×4=16；LoRA 默认学习率 2e-4，full 仍为 2e-5，可通过 `--learning-rate` 覆盖。LoRA 是否达到预期精度仍通过同一留出测试集验收。

训练入口也读取 `.env` 中的 `CH10_*` 配置，CLI 优先；未设置时走原 full 默认值。训练目录仍为 `data/ch10/model`，其中 `adapter/` 仅保存适配器备份，根目录是已合并的完整模型，`training_config.json` 记录本轮模式和参数；阈值仍在 `threshold.json`。

训练后仍按顺序执行 `make ch10-eval ML_UV_ARGS="--extra cpu"`、`make ch10-export ML_UV_ARGS="--extra cpu"`。启动推理只需要 inference 组和导出的模型产物，不需要 ml 组。容器内若设置 `UV_NO_SYNC=1`，必须先在镜像构建阶段安装相应依赖；本轮不调整尚未定稿的训练容器方案。

Langfuse Cloud 填写 `.env.example` 所列三项配置并重启后端。`make cost-report` 和页面的报告入口不变；需要 Cloud/v4 的 Observations v2 API，不再兼容旧自托管 v3 查询接口。

## 5. 本轮验证结果

- 29 项相关测试通过：25 项统计、配置旁路、语料、阈值、API 和作业契约测试，以及 4 项真实模型训练测试。
- full/LoRA 均执行了本地小型 BERT 的真实训练、评估与 ONNX 导出，导出校验无标签不一致；分批服务与 Torch 概率在容差内一致。
- 测试使用 Torch 2.14.1+cpu、Transformers 5.14.1、PEFT 0.21.1、ONNX Runtime 1.27.0。依赖以更新后的 uv.lock 为准。
- 这证明流程兼容，不代表真实 RoBERTa 数据集的 F1 或服务器耗时已验证。真实 Langfuse 账号、MySQL/Milvus 和 Ubuntu 容器联调留在部署阶段。
- 原导出链路仍使用 TorchScript 导出器，有上游弃用及 tracing 提示；本轮已用变长输入、不同批量和 Torch 对照验证，未改写导出路径。
