"""Markdown 结构感知的文本切片（RAG 链路唯一分片实现）。

为什么放在 ``core/rag`` 而不是 ``app.utils``：切片口径是本系统检索质量的主旋钮，
后续还要持续调（标题路径、表格/代码原子块、token 计量、上下文注入都排在这里），
它虽不碰业务表，但必须与解析、向量化一起演进；工具层只放无策略的纯函数。

三段式：

1. **块化** :func:`_scan_blocks`：按 Markdown 结构把正文切成不可再分的块——
   标题、段落、围栏代码、``$$`` 公式、表格；每块记录自身类型与所属标题路径；
2. **装配** :func:`_pack_blocks`：贪心把相邻块装进同一个粗块，标题永远粘在其后
   正文之前（不产生孤立标题），表格/代码/公式作为原子块不跨粗块切分；
3. **兜底切分** :func:`_split_oversize`：粗块仍超长时才交给
   ``RecursiveCharacterTextSplitter``（只用段落与标点分隔符），表格按行切并重复表头。

重叠只在「同一个粗块被硬切」时生效：结构边界（标题之间、段落之间、表格前后）本身
语义完整，在边界上再叠一段重叠只会让相邻切片互相污染，也会让检索双通道重复召回同段内容。
"""

import re
from dataclasses import dataclass, field
from typing import Any

from fastapi import status
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.config.setting import settings
from app.core.exceptions import CustomException

# 块类型：只决定装配与兜底策略，不落库（落库的来源类型仍由调用方决定）
BLOCK_TEXT = "text"
BLOCK_HEADING = "heading"
BLOCK_TABLE = "table"
BLOCK_CODE = "code"
BLOCK_FORMULA = "formula"

# 连续 3 行以上换行压缩为 2 行
_BLANK_LINES = re.compile(r"\n{3,}")
# Markdown 标题：``#`` 1–6 级 + 标题正文（允许行尾闭合的 ``#``）
_MARKDOWN_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
# 围栏代码起始标记：`` 与 ~~ 各三连
_FENCES: tuple[str, ...] = ("```", "~~~")
# 表格分隔行：``| --- | :---: |``（容忍没有前导竖线的写法）
_TABLE_DIVIDER = re.compile(r"^\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)*\|?$")
# HTML 表格：MinerU 新版默认把表格还原成 ``<table>`` 而不是管道表格
_HTML_TABLE_START = "<table"
_HTML_TABLE_END = "</table>"
# HTML 表格按行切分的标记。MinerU 常把整张表输出成一行（无换行），只能按标签边界切
_HTML_TABLE_OPEN = re.compile(r"<table\b[^>]*>", re.IGNORECASE)
_HTML_TABLE_HEAD = re.compile(r"<thead\b.*?</thead>", re.IGNORECASE | re.DOTALL)
_HTML_TABLE_ROW = re.compile(r"<tr\b.*?</tr>", re.IGNORECASE | re.DOTALL)

# 兜底切分的分隔符：只剩段落与标点。Markdown 结构符已在块化阶段消费掉，
# 留着它们反而会让递归切分先把标题/表格行劈开
_PUNCTUATION_SEPARATORS: tuple[str, ...] = (
    "\n\n",
    "\n",
    "。",
    "！",
    "？",
    "；",
    ".",
    "!",
    "?",
    ";",
    "，",
    ",",
    " ",
    "",
)

# ``sbp_ai_kb_chunk.heading`` 是 varchar(500)：路径超长时从左侧裁外层标题，保住叶子节点
_MAX_HEADING_CHARS = 500
# 结构块（代码/公式）最长容忍 = 分片长度 × 该倍数，超过才按行硬切
_ATOMIC_LIMIT_FACTOR = 3
# 短片段并回前一片的容忍上限倍数：允许略超分片长度，但不让两片拼成一片半
_MERGE_LIMIT_FACTOR = 1.5


def embedding_text(content: str, heading: str | None) -> str:
    """向量化输入的拼接口径：标题路径在正文之前，两者之间空一行。

    只嵌正文会让「4.3 节讲的流量减少时间」这类靠标题定位的问题召不回对应片段——
    标题路径本来就是这段内容的主语，而 query 不会替它补上。落库与返回的正文仍是
    原文（``content`` 不变），拼接口径只作用于向量，不影响 snippet 回溯。
    """
    title = (heading or "").strip()
    return f"{title}\n{content}" if title else content


@dataclass(frozen=True)
class TextChunk:
    """一个文本切片：content 为最终写入的原文，meta 由调用方原样携带。"""

    index: int
    content: str
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def embed_text(self) -> str:
        """送入向量模型的文本（标题路径 + 正文，口径见 :func:`embedding_text`）。"""
        return embedding_text(self.content, self.meta.get("section_title"))


@dataclass(frozen=True)
class _Block:
    """块化阶段的中间单元：``kind`` 决定装配与兜底策略，``heading`` 是所属标题路径。"""

    kind: str
    text: str
    heading: str | None


@dataclass(frozen=True)
class _Packed:
    """装配后的粗块：``kind`` 取块内非标题块类型的唯一值，混合时回落 ``text``。"""

    kind: str
    text: str
    heading: str | None


def normalize_text(raw: str) -> str:
    """统一换行、去 BOM、压缩连续空行与行尾空白。"""
    text = raw.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return _BLANK_LINES.sub("\n\n", text).strip()


def _heading_path(stack: list[tuple[int, str]]) -> str | None:
    """把标题栈压成 ``一级 > 二级 > 三级``；超长时从左裁，保留最靠近正文的层级。

    只存叶子标题（改造前的口径）会让「## 时限」这类同名小节在多个章节下无法区分，
    而 ``heading`` 列本来就是「所属标题路径」，路径才是它的语义。
    """
    if not stack:
        return None
    titles = [title for _, title in stack]
    path = " > ".join(titles)
    while len(path) > _MAX_HEADING_CHARS and len(titles) > 1:
        titles.pop(0)
        path = "… > " + " > ".join(titles)
    if len(path) <= _MAX_HEADING_CHARS:
        return path
    # 单个标题自身就超限：只能硬截（保留前缀，叶子标题通常自带区分信息）
    return path[: _MAX_HEADING_CHARS - 1] + "…"


def _is_table_header(lines: list[str], index: int) -> bool:
    """``lines[index]`` 是表格首行：本行含竖线，且下一行是 ``| --- |`` 分隔行。"""
    if index + 1 >= len(lines):
        return False
    if "|" not in lines[index]:
        return False
    return bool(_TABLE_DIVIDER.match(lines[index + 1].strip()))


def _table_header_index(lines: list[str]) -> int | None:
    """在一段文本里定位表格首行（跳过前面拼进来的标题行）。"""
    for index in range(len(lines) - 1):
        if _is_table_header(lines, index):
            return index
    return None


def _consume_atomic(lines: list[str], index: int, terminator: str, kind: str) -> tuple[str, int]:
    """从 ``index`` 起收下一个原子块，返回 ``(块正文, 下一行号)``。

    ``terminator`` 的语义按块类型区分：代码/公式是「闭合标记」（找不到就吃到文末），
    表格是「续行特征」（含竖线的连续行都属于同一张表）。表格续行判定放宽到「含竖线」，
    是为了兼容没有前导 ``|`` 的写法；误并进来的正文只会让该块按表格策略切，不会丢内容。
    """

    def collect(start: int, end: int) -> str:
        return "\n".join(lines[start : end + 1]).strip("\n")

    if kind == BLOCK_TABLE:
        end = index
        while end + 1 < len(lines) and "|" in lines[end + 1]:
            end += 1
        return collect(index, end), end + 1
    if kind == BLOCK_FORMULA and lines[index].strip().count("$$") >= 2:
        # 单行公式 $$...$$：起止标记在同一行，不必再找闭合
        return lines[index].strip(), index + 1

    end = index + 1
    while end < len(lines) and not lines[end].strip().startswith(terminator):
        end += 1
    end = min(end, len(lines) - 1)
    return collect(index, end), end + 1


def _scan_blocks(text: str) -> list[_Block]:
    """按 Markdown 结构把正文切成块，顺序与原文一致。"""
    lines = text.split("\n")
    total = len(lines)
    blocks: list[_Block] = []
    stack: list[tuple[int, str]] = []
    pending: list[str] = []

    def flush() -> None:
        body = "\n".join(pending).strip("\n").strip()
        pending.clear()
        if body:
            blocks.append(_Block(BLOCK_TEXT, body, _heading_path(stack)))

    index = 0
    while index < total:
        line = lines[index]
        stripped = line.strip()
        heading = _MARKDOWN_HEADING.match(stripped)
        fence = next((mark for mark in _FENCES if stripped.startswith(mark)), None)

        if not stripped:
            # 空行是段落边界：它是「段落优先」的唯一依据
            flush()
            index += 1
        elif fence:
            flush()
            body, index = _consume_atomic(lines, index, fence, BLOCK_CODE)
            blocks.append(_Block(BLOCK_CODE, body, _heading_path(stack)))
        elif stripped.startswith("$$"):
            flush()
            body, index = _consume_atomic(lines, index, "$$", BLOCK_FORMULA)
            blocks.append(_Block(BLOCK_FORMULA, body, _heading_path(stack)))
        elif heading:
            flush()
            level = len(heading.group(1))
            # 同级或更深的标题先出栈：路径只保留当前分支的祖先链
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, heading.group(2).strip()))
            blocks.append(_Block(BLOCK_HEADING, stripped, _heading_path(stack)))
            index += 1
        elif stripped.lower().startswith(_HTML_TABLE_START):
            flush()
            end = index
            while end + 1 < total and _HTML_TABLE_END not in lines[end].lower():
                end += 1
            end = min(end, total - 1)
            body = "\n".join(lines[index : end + 1]).strip("\n")
            blocks.append(_Block(BLOCK_TABLE, body, _heading_path(stack)))
            index = end + 1
        elif _is_table_header(lines, index):
            flush()
            body, index = _consume_atomic(lines, index, "|", BLOCK_TABLE)
            blocks.append(_Block(BLOCK_TABLE, body, _heading_path(stack)))
        else:
            pending.append(line)
            index += 1

    flush()
    return blocks


def _pack_blocks(blocks: list[_Block], chunk_size: int) -> list[_Packed]:
    """把块贪心装进不超过 ``chunk_size`` 的粗块（标题与正文永远同块）。"""
    packed: list[_Packed] = []
    current: list[_Block] = []
    size = 0
    has_body = False

    def emit() -> None:
        nonlocal current, size, has_body
        if not current:
            return
        kinds = {block.kind for block in current if block.kind != BLOCK_HEADING}
        packed.append(
            _Packed(
                kind=kinds.pop() if len(kinds) == 1 else BLOCK_TEXT,
                text="\n\n".join(block.text for block in current),
                # 取最后一个非空标题：粗块可能装了「# 一级 + ## 二级 + 正文」，
                # 贴近正文的叶子路径才是这段内容的主语，取第一个会退回外层标题
                heading=next((block.heading for block in reversed(current) if block.heading), None),
            )
        )
        current, size, has_body = [], 0, False

    for block in blocks:
        # 标题只能开启一个新粗块，不能续在上一块尾巴后面
        if block.kind == BLOCK_HEADING and has_body:
            emit()
        # 已经装了正文且装不下：先收尾。标题走不到这里（上一行已保证它开头）
        if has_body and size + len(block.text) + 2 > chunk_size:
            emit()
        current.append(block)
        size += len(block.text) + 2
        has_body = has_body or block.kind != BLOCK_HEADING
    emit()
    return packed


def _recursive(text: str, chunk_size: int, overlap: int) -> list[str]:
    """标点级递归切分：只处理超长段落，结构符已在块化阶段消费掉。"""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=min(overlap, max(0, chunk_size - 1)),
        separators=list(_PUNCTUATION_SEPARATORS),
        keep_separator=True,
        strip_whitespace=True,
        length_function=len,
    )
    return splitter.split_text(text)


def _split_table(table: str, chunk_size: int) -> list[str] | None:
    """按行切 Markdown 管道表格并给每片重复表头：丢掉表头的数据行在检索里等于没有主语。

    入参可能是「标题 + 表格」装配成的粗块，所以先定位真实的表头行，
    表格之前的标题行只跟第一片走（后续片的表头已自带上下文）。

    找不到管道表头时返回 ``None``：HTML ``<table>`` 交给 :func:`_split_html_table`，
    两者都处理不了就由调用方整块保留（按标点硬切只会把标签劈成两半）。
    """
    lines = table.split("\n")
    start = _table_header_index(lines)
    if start is None or len(lines) - start < 3:
        return None
    prefix, header, body = lines[:start], lines[start : start + 2], lines[start + 2 :]
    prefix_size = sum(len(line) + 1 for line in prefix)
    header_size = sum(len(line) + 1 for line in header)
    pieces: list[str] = []
    rows: list[str] = []
    # 首片额外背 prefix（标题/说明）的长度，后续片只背表头
    size = header_size + prefix_size
    for row in body:
        if rows and size + len(row) + 1 > chunk_size:
            pieces.append("\n".join(header + rows))
            rows, size = [], header_size
        rows.append(row)
        size += len(row) + 1
    if rows:
        pieces.append("\n".join(header + rows))
    if prefix:
        pieces[0] = "\n".join(prefix + [pieces[0]])
    return pieces


def _split_html_table(table: str, chunk_size: int) -> list[str] | None:
    """按 ``<tr>`` 切 HTML 表格并给每片重复 ``<table>`` 与表头。

    MinerU 还原的表格常常整张只有一行、没有换行，按行号切不动，只能按标签边界切；
    表头优先取 ``<thead>``，没有就取第一条 ``<tr>``（与管道表格「首行是表头」同口径）。
    切不出至少两片时返回 ``None``，交给调用方整块保留。
    """
    open_match = _HTML_TABLE_OPEN.search(table)
    if open_match is None:
        return None
    open_tag = open_match.group(0)
    prefix = table[: open_match.start()].strip("\n")

    body = table[open_match.end() :]
    head_match = _HTML_TABLE_HEAD.search(body)
    head = head_match.group(0) if head_match else ""
    if head_match:
        body = body[: head_match.start()] + body[head_match.end() :]
    rows = _HTML_TABLE_ROW.findall(body)
    if not head and rows:
        head, rows = rows[0], rows[1:]
    if len(rows) < 2:
        return None

    def compose(subset: list[str]) -> str:
        return "\n".join([open_tag, *([head] if head else []), *subset, _HTML_TABLE_END])

    # 每片都要背的开销：<table> + 表头 + </table>；首片额外背标题行
    fixed = len(open_tag) + len(head) + len(_HTML_TABLE_END) + 3
    pieces: list[str] = []
    bucket: list[str] = []
    size = fixed + len(prefix)
    for row in rows:
        if bucket and size + len(row) > chunk_size:
            pieces.append(compose(bucket))
            bucket, size = [], fixed
        bucket.append(row)
        size += len(row)
    if bucket:
        pieces.append(compose(bucket))
    if len(pieces) < 2:
        return None
    if prefix:
        pieces[0] = f"{prefix}\n{pieces[0]}"
    return pieces


def _split_oversize(block: _Packed, *, chunk_size: int, overlap: int) -> list[str]:
    """粗块超长时的兜底切分；不超长原样返回。"""
    if len(block.text) <= chunk_size:
        return [block.text]
    if block.kind == BLOCK_TABLE:
        return _split_table(block.text, chunk_size) or _split_html_table(block.text, chunk_size) or [block.text]
    if block.kind in (BLOCK_CODE, BLOCK_FORMULA) and len(block.text) <= chunk_size * _ATOMIC_LIMIT_FACTOR:
        # 代码与公式是逻辑上不可分的单元：宁可超一点，也别从中间劈开
        return [block.text]
    return _recursive(block.text, chunk_size, overlap)


def _merge_short(pieces: list[tuple[str, _Packed]], *, chunk_size: int, min_chars: int) -> list[tuple[str, _Packed]]:
    """把过短的片段并回前一片（不越过上限的 1.5 倍，也不跨越标题边界）。

    以标题行起头的片段代表一个新章节，并回前一片会把章节边界和标题路径一起抹掉；
    两侧保留空行是为了让合并后的正文仍然是两个可读段落，而不是粘成一行。
    """
    merged: list[tuple[str, _Packed]] = []
    for content, block in pieces:
        is_heading_start = bool(_MARKDOWN_HEADING.match(content.split("\n", 1)[0].strip()))
        if merged and len(content) < min_chars and not is_heading_start:
            head_text, head_block = merged[-1]
            if len(head_text) + len(content) + 2 <= int(chunk_size * _MERGE_LIMIT_FACTOR):
                merged[-1] = (f"{head_text}\n\n{content}", head_block)
                continue
        merged.append((content, block))
    return merged


def split_text(
    text: str,
    *,
    chunk_size: int | None = None,
    overlap: int | None = None,
    meta: dict[str, Any] | None = None,
) -> list[TextChunk]:
    """分片入口：返回按顺序编号的分片列表（空文本返回空列表）。"""
    size = settings.PARSE_CHUNK_SIZE if chunk_size is None else chunk_size
    gap = settings.PARSE_CHUNK_OVERLAP if overlap is None else overlap
    if size <= 0:
        raise CustomException(msg="分片长度必须大于 0", status_code=status.HTTP_400_BAD_REQUEST)
    if gap < 0 or gap >= size:
        raise CustomException(msg="重叠字符数须 >=0 且小于分片长度", status_code=status.HTTP_400_BAD_REQUEST)

    normalized = normalize_text(text)
    if not normalized:
        return []

    base_meta = dict(meta or {})
    pieces: list[tuple[str, _Packed]] = []
    for block in _pack_blocks(_scan_blocks(normalized), size):
        for piece in _split_oversize(block, chunk_size=size, overlap=gap):
            if piece.strip():
                pieces.append((piece.strip(), block))
    # 短块阈值不得超过分片长度，否则「小尺寸联调」会把所有切片都判成短块
    pieces = _merge_short(pieces, chunk_size=size, min_chars=min(settings.PARSE_CHUNK_MIN, size))

    chunks: list[TextChunk] = []
    for content, block in pieces:
        chunk_meta = dict(base_meta)
        if block.heading:
            chunk_meta["section_title"] = block.heading
        chunks.append(TextChunk(index=len(chunks), content=content, meta=chunk_meta))
    return chunks
