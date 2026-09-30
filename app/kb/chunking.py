import re

from langchain_core.documents import Document
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter

HEADERS = [("#", "h1"), ("##", "h2"), ("###", "h3"), ("####", "h4")]
# 中文无词边界,分隔符优先段落/换行,再句末标点,最后逐字
CJK_SEPARATORS = ["\n\n", "\n", "。", "！", "？", "；", "!", "?", ";", "，", " ", ""]


def split_sections(md: str) -> list[Document]:
    # strip_headers=True ，标题从正文里被摘掉了。标题不是丢了，它在 sec.metadata 里，等会装配的时候另有用处。
    splitter = MarkdownHeaderTextSplitter(headers_to_split_on=HEADERS, strip_headers=True)
    return splitter.split_text(md)


def recursive_split(text: str, chunk_size: int, chunk_overlap: int = 0) -> list[str]:
    """
    递归字符切分，仅负责文本切块，**关闭原生字符级重叠**
    注意：原生chunk_overlap是按字符截取，容易产生半截破碎语句，
    重叠逻辑交给上层 apply_sentence_overlap 做整句重叠，因此调用时传 chunk_overlap=0
    """
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size, chunk_overlap=chunk_overlap,
        separators=CJK_SEPARATORS, is_separator_regex=False, length_function=len,
    )
    return splitter.split_text(text)


# 中文句子正则：匹配以句号/感叹号/问号/省略号/换行结尾的完整句子，或末尾剩余片段
_SENT_RE = re.compile(r"[^。！？!?…\n]*[。！？!?…\n]|[^。！？!?…\n]+$")


def _split_sentences(text: str) -> list[str]:
    """把文本拆分为句子列表，基于中文句末标点切分"""
    return [m for m in _SENT_RE.findall(text) if m]


def _trailing_sentences(text: str, max_chars: int) -> str:
    """
    提取文本末尾若干**完整句子**，用作块之间的重叠片段
    - 总字符尽量不超过 max_chars
    - 若单个句子本身超长：强制保留完整句子，允许轻微超出字符限制，避免半截破碎文本

    实现逻辑：从末尾反向遍历句子，累加长度，接近上限停止；
    `out and` 判断：保证至少放入1句，不会因为单句超长直接丢弃全部重叠内容
    """
    out: list[str] = []
    total = 0
    # 倒序遍历句子：从文本最后一句向前收集
    for s in reversed(_split_sentences(text)):
        # out非空时，再加这句会超限则停止；out为空时（还没收集任何句子），超长也必须收下当前句
        if out and total + len(s) > max_chars:
            break
        out.insert(0, s)
        total += len(s)
    return "".join(out)


def apply_sentence_overlap(chunks: list[str], overlap: int) -> list[str]:
    """
    【整句级块重叠】
    在字符切分得到的块列表上，为每个后续块前置上一块末尾的完整句子片段。
    对比原生字符重叠：规避截断产生的半截碎句，提升RAG检索上下文连贯性。

    :param chunks: recursive_split(chunk_overlap=0)输出的无重叠文本块
    :param overlap: 重叠区域目标最大字符数（只是参考，单句超长会略微超限）
    :return: 已附加整句重叠的新块列表
    """
    if not chunks:
        return []
    out = [chunks[0]]  # 第一个块保持原样，不需要前置重叠
    for i in range(1, len(chunks)):
        # 获取上一个块末尾的完整句子作为重叠前缀
        ov = _trailing_sentences(chunks[i - 1], overlap)
        # 有重叠片段就拼到当前块开头，无则直接使用原块
        out.append(ov + chunks[i] if ov else chunks[i])
    return out


_TABLE_SEP_RE = re.compile(r"^\s*\|?[\s:|-]+\|?\s*$")


# 切分表格：
# 1. 先定位 Markdown 表格表头：找到后面紧跟`---`分隔线的行，表头前面的普通文本当作前言。
# 2. 如果表格数据行数≤max_rows，直接原样返回整块，不拆分。
# 3. 大表按数据行分批切割：第一块带上**前言 + 表头 + 分隔线 + 一批数据行**；后续每块只复用**表头 + 分隔线 + 对应批数据行**。
# 4. 保证每个切分出的子块都是合法完整 Markdown 表格，不会出现无表头的残缺表格片段。
def _find_table_header(lines: list[str]) -> int:
    """返回表头行下标(其后紧跟 --- 分隔行);无表格返回 -1。
    允许表格前有若干散文行(不再要求首行即表头)。"""
    for i in range(len(lines) - 1):
        if (lines[i].lstrip().startswith("|")
                and _TABLE_SEP_RE.match(lines[i + 1]) and "-" in lines[i + 1]):
            return i
    return -1


def is_table_block(text: str) -> bool:
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    return _find_table_header(lines) != -1


def split_table_rows(table_md: str, max_rows: int) -> list[str]:
    """大表格按行切,每块重贴表头行 + 分隔行;不超 max_rows 数据行则整块返回。
    表头前的散文行作为前言,保留在首块(不丢),后续块只带表头。"""
    lines = [ln for ln in table_md.strip().splitlines() if ln.strip()]
    idx = _find_table_header(lines)
    if idx == -1:
        return [table_md.strip()]
    preamble, header, sep, rows = lines[:idx], lines[idx], lines[idx + 1], lines[idx + 2:]
    if len(rows) <= max_rows:
        return [table_md.strip()]
    out: list[str] = []
    for j, i in enumerate(range(0, len(rows), max_rows)):
        group = rows[i:i + max_rows]
        block = [*preamble, header, sep, *group] if j == 0 else [header, sep, *group]
        out.append("\n".join(block))
    return out
