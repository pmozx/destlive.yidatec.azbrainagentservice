"""文本切片工具：把长文本切成「长度可控 + 相邻带重叠」的片段。

口径：
1. 切片内核用 ``RecursiveCharacterTextSplitter``：段落 → 换行 → 中英文句末标点 → 逗号 → 空格
   逐级回退，切不开才定长硬切，任一单元都可装进一个块；
2. 长度按**字符数**计算（中英混排不折算 token），与 ``PARSE_CHUNK_SIZE`` 的历史口径一致；
3. 重叠只保留上一块尾部 ``overlap`` 字符，避免答案被切断；
4. 分片索引从 0 连续递增，由调用方按自身表结构对齐唯一约束。

该模块只依赖配置、异常与 langchain-text-splitters，不感知任何业务表，可在任意模块复用。
"""

import re
from dataclasses import dataclass, field
from typing import Any

from fastapi import status
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.config.setting import settings
from app.core.exceptions import CustomException

# 连续 3 行以上换行压缩为 2 行
_BLANK_LINES = re.compile(r"\n{3,}")

# 递归分隔符：空行（段落）→ 换行 → 中英文句末标点 → 逗号 → 空格 → 逐字硬切。
# 句末标点放前面能保证「一句完整的话」不会被逗号拆开，中文标点必须显式列出。
_SEPARATORS: tuple[str, ...] = ("\n\n", "\n", "。", "！", "？", "；", ".", "!", "?", ";", "，", ",", " ", "")


@dataclass(frozen=True)
class TextChunk:
    """一个文本切片：content 为最终写入的原文，meta 由调用方原样携带。"""

    index: int
    content: str
    meta: dict[str, Any] = field(default_factory=dict)


def normalize_text(raw: str) -> str:
    """统一换行、去 BOM、压缩连续空行与行尾空白。"""
    text = raw.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return _BLANK_LINES.sub("\n\n", text).strip()


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

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=size,
        chunk_overlap=gap,
        separators=list(_SEPARATORS),
        keep_separator=True,
        strip_whitespace=True,
        length_function=len,
    )
    base_meta = dict(meta or {})
    contents = [piece for piece in splitter.split_text(normalized) if piece.strip()]
    return [TextChunk(index=index, content=content, meta=dict(base_meta)) for index, content in enumerate(contents)]
