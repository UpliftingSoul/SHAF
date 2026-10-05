"""对话知识挖掘：统一结构化提取，结果暂存、去重后交由人工审核。"""

import logging
from datetime import datetime

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

from app.core import llm
from app.core.prompts import MINING_PROMPT
from app.db import repository
from app.kb import dedup


class QaPair(BaseModel):
    question: str
    answer: str


class QaExtraction(BaseModel):
    """LLM 结构化输出 schema:用两个并列标量数组一一对应。
    实测 glm-5.2 的 anthropic 兼容上游对嵌套对象数组(list[对象])的工具 schema 会 502,
    标量 list[str] 正常,故不用 list[QaPair],改并列数组规避。"""

    questions: list[str] = Field(
        default_factory=list, description="问法列表,与 answers 一一对应;无可复用问答则为空"
    )
    answers: list[str] = Field(
        default_factory=list, description="答案列表,与 questions 一一对应"
    )


async def extract_qa(conversation_texts: list[str], model=None) -> list[QaPair]:
    """
    异步从多段对话文本中挖掘提取问答对，返回标准化QaPair对象列表
    适配glm‑5.2接口限制：接收双数组格式结构化输出，再还原为问答对象
    """
    # 保留模型注入入口，结构化提取统一走 function calling、校验和重试。
    chain = MINING_PROMPT | llm.structured(QaExtraction, client=model)
    # 将多段对话用分隔符拼接，异步调用大模型获取结构化结果
    result: QaExtraction = await chain.ainvoke(
        {"conversations": "\n---\n".join(conversation_texts)}
    )

    qs, ans = result.questions, result.answers
    # 防御处理：大模型偶尔输出两个数组长度对不齐，避免问答错位
    if len(qs) != len(ans):
        logger.warning("挖知识并列数组长度不一致 questions=%d answers=%d,按较短对齐", len(qs), len(ans))
    # 取较短的数组长度，截断防止索引越界
    n = min(len(qs), len(ans))
    # 通过下标映射，把两套平行字符串数组还原为QaPair问答对象列表返回
    return [QaPair(question=qs[i], answer=ans[i]) for i in range(n)]


async def _load_conversation_texts() -> list[tuple[str, str]]:
    """返回 [(source_ref, 对话文本)];对话文本由该会话的 user/assistant 消息拼成。"""
    convs = await repository.list_conversations_with_messages()
    out: list[tuple[str, str]] = []
    for conv_id, msgs in convs:
        lines = [f"{m.role}: {m.content}" for m in msgs if m.content]
        if lines:
            out.append((f"conv:{conv_id}", "\n".join(lines)))
    return out


async def mine(batch_size: int = 20, model=None) -> dict:
    """
    可重跑任务：从对话历史批量挖掘问答知识
    流程：对话加载 → LLM分批抽取 → 写入暂存表 → 全局去重 → 标记待人工审核
    【重要设计约束】
    模型抽取得到的问答**不直接写入正式知识库knowledge_chunks**。
    对话挖掘产出内容具备风险：回答可能仅适用于单个业务工单、混杂订单/地址等个人敏感信息，
    也可能把客服无实质价值话术提炼成答案；自动入库会污染向量知识库，导致检索输出错误内容。
    挖掘仅输出至暂存层；必须由人工页面审核采纳后，才允许写入正式知识库。

    批次隔离说明：使用batch_no区分本次任务数据，避免任务中途崩溃遗留的旧extracted残骸混入本轮去重逻辑，
    保证计算结果可追溯、不受历史脏数据干扰。

    人工入库入口：/staging/approve接口；仅允许处理kept状态记录；
    采纳后content_type标记为mined，用于区分文档切片知识与对话挖掘生成知识；弃用记录保留数据库，留存审计溯源信息。
    """
    # 加载待处理原始对话数据源
    sources = await _load_conversation_texts()
    # 生成本次任务唯一批次标识，用于数据隔离
    batch_no = datetime.now().strftime("mine-%Y%m%d%H%M%S")

    # --------步骤1：分批调用大模型抽取问答对，写入暂存表，初始状态extracted(已提取待去重)--------
    for start in range(0, len(sources), batch_size):
        batch = sources[start:start + batch_size]
        # 对一批对话执行问答抽取
        pairs = await extract_qa([t for _, t in batch], model=model)
        # 将抽取结果写入qa_extraction_staging暂存表
        for p in pairs:
            await repository.insert_staging(batch_no, batch[0][0], p.question, p.answer)

    # --------步骤2：批次内全局去重--------
    # 仅读取**当前batch_no**对应的暂存数据，过滤历史任务崩溃残留脏数据
    staged = [s for s in await repository.list_staging_by_status("extracted") if s.batch_no == batch_no]
    # 获取正式库已存在全部问题文本，用于跨库去重
    existing = await repository.list_all_questions()
    # 执行去重：对比已有知识库+本批次内部重复
    kept, discarded = dedup.dedupe(staged, existing)

    # 更新暂存记录状态：kept=去重通过→待人工审核；discarded=去重直接丢弃
    await repository.set_staging_status([s.id for s in kept], "kept")
    await repository.set_staging_status([s.id for s in discarded], "discarded")

    # --------步骤3：mine任务到此结束，正式知识库写入完全交由人工审核接口--------
    # 返回本次挖掘统计指标
    return {
        "sources": len(sources),
        "extracted": len(staged),
        "kept": len(kept),
        "discarded": len(discarded)
    }

