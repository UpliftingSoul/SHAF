"""
ch09 数据飞轮流水线：将低置信问题标准化、查重并归并到 review_queue 待审队列。

批处理采用定时串行模式，不做近实时：
- 以 low_confidence_questions.matched_review_id IS NULL 作为待处理游标，
  成功归并后回写 review_id；失败则保持未归并，留待下一轮重试。
- 同批问题逐条处理且每条重新读取候选，使前一条刚创建的知识缺口能立即参与后续查重。
- 命中待审/通过/驳回任一候选都只累加 occurrence_count；驳回属于终审，不自动复活。
- 模型解析失败或返回候选集外 id 时跳过并告警，不写错误归并结果。

标准化与查重合并为一次结构化模型调用，输出 FAQ 式标准问题、候选匹配 id 和示例答案。
"""

import logging

from pydantic import BaseModel, Field

from app.core import llm
from app.core.llm import get_chat_model
from app.core.prompts import FLYWHEEL_NORMALIZE_PROMPT
from app.db import repository

logger = logging.getLogger(__name__)


class NormalizeResult(BaseModel):
    """飞轮标准化/查重的一次结构化输出，供后续归并或新建待审缺口。"""

    normalized_question: str = Field(description="FAQ 式标准问题")
    matched_question_id: int | None = Field(default=None, description="命中候选 id,无同类为 null")
    ai_suggested_answer: str = Field(default="", description="示例答案备查")


def _chain():
    """构造标准化 + 查重链，强制模型按 NormalizeResult 返回结构化结果。"""

    return FLYWHEEL_NORMALIZE_PROMPT | llm.structured(NormalizeResult)


async def normalize_and_match(
    raw_question: str, candidates: list[dict]
) -> NormalizeResult:
    """
    一次模型调用同时完成口语问题标准化和已有缺口查重，并生成示例答案备查。
    candidates 提供可匹配的 review_queue id；模型返回的 matched_question_id
    还会在 process_pending 中再次校验，不能仅凭结构化类型约束直接信任。
    """
    # 标准化与查重本就依赖同一份原问题 + 候选集合，合并一次调用可避免重复喂上下文。
    cand_text = "\n".join(
        f"- id={c['id']}: {c['normalized_question']}" for c in candidates
    ) or "(无候选)"

    return await _chain().ainvoke(
        {"raw_question": raw_question, "candidates": cand_text}
    )


async def process_pending(limit: int = 50) -> dict:
    """
    批量处理 matched_review_id 尚为空的低置信问题：
    逐条标准化并查重，命中则累计已有缺口次数；否则新建待审项，最后回写归并落点。
    单条失败只计入 skipped 且不推进游标，保证后续批次仍可重新处理。
    """
    # matched_review_id IS NULL 即待处理游标；已经成功回写落点的原问题不会再次被本批取出。
    rows = await repository.fetch_unmatched_low_conf(limit)
    stats = {"processed": 0, "merged": 0, "created": 0, "skipped": 0}

    # 刻意串行而非并发：前一条新建的缺口必须能被后一条同义问题立即看见并归并。
    for row in rows:
        # 每条现拉最新候选；多取第 201 条只为判断是否真的超过 200，
        # 避免候选恰好 200 条时误报“查重覆盖被截断”。
        fetched = await repository.list_review_candidates(limit=201)
        truncated = len(fetched) > 200
        candidates = fetched[:200]

        try:
            r = await normalize_and_match(row.raw_question, candidates)
        except Exception:
            # 模型/解析失败不写归并结果，matched_review_id 保持 NULL，下轮继续重试。
            logger.warning(
                "flywheel 标准化失败 lcq=%s(跳过,下轮重试)",
                row.id, exc_info=True
            )
            stats["skipped"] += 1
            continue

        mid = r.matched_question_id
        if mid is not None:
            # schema 只能保证 mid 是整数，不能保证它真来自候选集合；
            # 候选外 id 视为模型幻觉，禁止据此修改其他 review_queue 记录。
            if not any(c["id"] == mid for c in candidates):
                logger.warning(
                    "flywheel 幻觉 id=%s lcq=%s(候选里不存在,跳过下轮重试)",
                    mid, row.id
                )
                stats["skipped"] += 1
                continue

            # 无论候选当前是待审、通过还是驳回，都只累计出现次数；
            # 驳回是人工终审结果，不因同类问题再次出现而恢复成待审。
            await repository.increment_occurrence(mid)
            review_id = mid
            stats["merged"] += 1
        else:
            # 当前候选中没有同类缺口：以标准问题 + 示例答案新建一条 review_queue 待审项。
            review_id = await repository.insert_review_item(
                r.normalized_question, r.ai_suggested_answer or None
            )
            stats["created"] += 1

        # 归并/新建成功后最后回写落点，标记这条原问题已经进入对应知识缺口。
        await repository.set_matched_review(row.id, review_id)
        stats["processed"] += 1

        # 超过 200 条候选时查重范围并不完整，必须在日志中显式暴露这一降级状态。
        logger.info(
            "flywheel lcq=%s → review=%s(%s)%s", row.id, review_id,
            "归并" if mid is not None else "新建",
            " [候选已截断200,查重覆盖不全]" if truncated else ""
        )

    return stats