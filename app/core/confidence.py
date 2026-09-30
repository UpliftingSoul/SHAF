"""
ch09 证据置信度：量化“当前检索到的这批证据，是否足以支撑回答用户问题”。

置信度完全复用检索 / 精排阶段已有结果，不增加额外模型调用，由四个信号组成：
- top1_score：最强证据的 rerank_score；
- valid_count：达到有效分数线的证据数量，判断强证据是孤证还是成群；
- margin：Top1 与 Top2 的分差，衡量证据是否集中指向同一方向；
- key_clause_hit：Top3 是否命中 KEY_TERMS 中的关键条款词。

四项归一化后按固定权重加权得到 0~1 总分。权重作为模块常量保持稳定，不放入 settings；
评估集只负责校准 evidence_confidence_threshold，避免“权重 + 阈值”两层参数同时可调，导致效果难以归因。

除总分外同时保留四个原始信号，供低置信落池 reason 与 trace 排障使用。
"""

from dataclasses import dataclass

from app.kb.documents import KEY_TERMS


VALID_SCORE_FLOOR = 0.3   # rerank_score 达到该值才计为一条有效证据
VALID_COUNT_CAP = 3       # 有效证据数归一化封顶：3 条及以上按满分计算

# 固定组合权重：评估阶段只校准最终阈值，不再增加第二层可调旋钮。
W_TOP1, W_VALID, W_MARGIN, W_KEY = 0.5, 0.2, 0.2, 0.1


@dataclass
class EvidenceConfidence:
    """证据置信度结果：score 用于过闸，signals 用于留痕和解释判定原因。"""
    score: float    # 0~1 综合置信度
    signals: dict   # 四个原始信号


def _clip01(x: float) -> float:
    """将数值限制在 0~1，避免异常 rerank 分数影响各信号的归一化结果。"""
    return max(0.0, min(1.0, x))


def compute_evidence_confidence(hits: list[dict]) -> EvidenceConfidence:
    """
    根据已按精排顺序返回的 hits 计算四信号证据置信度。
    不额外调用模型；空召回直接返回 0 分，其余同时返回总分与原始信号。
    """
    if not hits:
        return EvidenceConfidence(
            0.0,
            {"top1_score": 0.0, "valid_count": 0,
             "margin": 0.0, "key_clause_hit": False}
        )

    scores = [float(h.get("rerank_score", 0.0)) for h in hits]
    top1 = scores[0]

    # margin 衡量证据聚焦度：Top1 断崖式领先比多条相近候选更明确；
    # 只有一条证据时没有 Top2 可比，直接用 top1 表示这条证据自身的聚焦强度。
    margin = top1 - scores[1] if len(scores) > 1 else top1

    # 统计达到有效线的强证据数量；最终计分最多按 3 条计算，避免数量无限放大权重。
    valid_count = sum(1 for s in scores if s >= VALID_SCORE_FLOOR)

    # hit 中没有入库时的 is_key_clause 字段，因此复用 ch03 的 KEY_TERMS 词表，
    # 在 Top3 问答文本中重新判断，保持关键条款识别口径一致。
    key_hit = any(
        any(t in f"{h.get('question', '')}{h.get('answer', '')}" for t in KEY_TERMS)
        for h in hits[:3]
    )

    # 四个信号统一归一到 0~1 后线性加权；权重和为 1，因此结果天然保持同一量纲。
    score = (
        W_TOP1 * _clip01(top1)
        + W_VALID * min(valid_count, VALID_COUNT_CAP) / VALID_COUNT_CAP
        + W_MARGIN * _clip01(margin)
        + W_KEY * (1.0 if key_hit else 0.0)
    )

    # 原始信号一并保留：事后看到低分时，可以判断究竟弱在强度、数量、聚焦度还是关键条款。
    return EvidenceConfidence(
        round(_clip01(score), 4),
        {"top1_score": top1, "valid_count": valid_count,
         "margin": round(margin, 4), "key_clause_hit": key_hit}
    )


def snapshot_from_hits(hits: list[dict], top_n: int = 3) -> list[dict]:
    """
    生成低置信落池使用的统一召回快照。
    默认保留 Top3 的问答原文、精排分数与章节位置，供审核页复盘当时实际检到了什么。
    """

    return [
        {"question": h.get("question", ""),
         "answer": h.get("answer", ""),
         "rerank_score": float(h.get("rerank_score", 0.0)),
         "section_path": h.get("section_path", "")}
        for h in hits[:top_n]
    ]
