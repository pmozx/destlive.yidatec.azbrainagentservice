"""分片：把解析出的长文本切成「长度可控 + 相邻带重叠」的片段。

口径：
1. 先按空行分段，段内再按句末标点(q/v)切，仍超长则定长硬切——保证任一单元都可被装进一个块；
2. 装块时贪心拼接，超限即出块，并把上一块尾部 overlap 字符带到新块开头，避免答案被切断在块边界；
3. 回填重叠后仍超长就放弃重叠（宁可少一点上下文，也不生成几乎全重复的块）；
4. 分片索引从 0 连续递增，与 ``sbp_ai_integration_chunk`` 的 (space_id, doc_id, chunk_index) 唯一约束对齐。
"""

import re
from dataclasses import dataclass, field
from typing import Any

from fastapi import status

from app.config.setting import settings
from app.core.exceptions import CustomException

# 句末标点：中英文句号/问号/叹号/分号/换行
_SENTENCE_END: frozenset[str] = frozenset("。！？!?；;.\n")
# 段落分隔：一个及以上空行
_PARAGRAPH_SEP = re.compile(r"\n\s*\n")
# 连续 3 行以上换行压缩为 2 行
_BLANK_LINES = re.compile(r"\n{3,}")


@dataclass(frozen=True)
class TextChunk:
    """一个分片：内容是最终写库的原文，meta 会原样带到向量表。"""

    index: int
    content: str
    meta: dict[str, Any] = field(default_factory=dict)


def normalize_text(raw: str) -> str:
    """统一换行、去 BOM、压缩连续空行与行尾空白。"""
    text = raw.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return _BLANK_LINES.sub("\n\n", text).strip()


def _split_paragraph(paragraph: str, limit: int) -> list[str]:
    """超长段落按句末标点切，仍超长的单片定长硬切。"""
    pieces: list[str] = []
    buffer = ""
    for char in paragraph:
        buffer += char
        # 句末标点且已积累到半个块长才切，避免把短句切得太碎
        if char in _SENTENCE_END and len(buffer) >= limit // 2:
            pieces.append(buffer)
            buffer = ""
    if buffer:
        pieces.append(buffer)

    result: list[str] = []
    for piece in pieces:
        while len(piece) > limit:
            result.append(piece[:limit])
            piece = piece[limit:]
        if piece.strip():
            result.append(piece)
    return result


def _build_units(text: str, limit: int) -> list[str]:
    """把全文拆成不超过 limit 的装配单元（段落优先，超长段落再细分）。"""
    units: list[str] = []
    for paragraph in _PARAGRAPH_SEP.split(text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) <= limit:
            units.append(paragraph)
        else:
            units.extend(_split_paragraph(paragraph, limit))
    return units


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
    contents: list[str] = []
    current = ""
    for unit in _build_units(normalized, size):
        candidate = f"{current}\n{unit}" if current else unit
        if len(candidate) <= size:
            current = candidate
            continue
        if not current:
            current = unit  # 理论上不会走到（单元已被限制在 size 内）
            continue
        contents.append(current.strip())
        tail = current[-gap:] if gap else ""
        candidate = f"{tail}\n{unit}" if tail else unit
        current = candidate if len(candidate) <= size else unit

    if current.strip():
        contents.append(current.strip())

    return [TextChunk(index=index, content=content, meta=dict(base_meta)) for index, content in enumerate(contents)]
