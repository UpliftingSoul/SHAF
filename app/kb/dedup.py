import re

# 正则：清除空白、标点、下划线；中文保留
_STRIP_RE = re.compile(r"[\s\W_]+", re.UNICODE)


def normalize_question(q: str) -> str:
    """问题标准化：去符号空格、转小写，用于比对去重"""
    return _STRIP_RE.sub("", q.strip().lower())


# 对新提取出的问答做去重，和库中已有问题比对；区分哪些要入库、哪些直接丢弃，分开返回两组结果。
def dedupe(items: list, existing_questions: list[str]) -> tuple[list, list]:
    """去重:staging 内部 + 对已有 knowledge 问法;返回 (kept, discarded)。"""

    # seen 一开始就装进了库里已有的全部问法，所以这一次遍历同时挡住两种重复，本批内部撞车的和跟存量撞车的。
    seen = {normalize_question(q) for q in existing_questions}
    kept, discarded = [], []
    for item in items:
        key = normalize_question(item.question)
        # 空问题 或 已存在 → 丢弃
        if not key or key in seen:
            discarded.append(item)
        else:
            seen.add(key)
            kept.append(item)
    return kept, discarded
