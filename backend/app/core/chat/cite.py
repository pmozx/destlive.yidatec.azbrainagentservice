"""引用快照与后处理（cites / snippet / 引用编号校验 / 问题拓展）。

这是 ``app/modules/integration_chat/cite.py`` 随模块删除后迁入 core 的等价实现——
原逻辑已被 ``tests/test_chat_cite.py`` 覆盖，不存在"另一份实现"，core 是唯一来源。

口径（沿用原实现，勿改）：
- 引用快照在**生成前**构建：编号 ``[1..n]`` 与正文 ``[n]`` 必须严格一致；
- snippet 取「关键词密度最高的窗口」，不是简单截断前 N 字；
- 生成后校验正文中的 ``[n]`` 是否落在 cites 范围内，越界编号摘除并记 warning
  （低成本强校验：模型编造 ``[9]`` 而只有 5 条引用时立刻可测）。

与业务解耦：命中项只要求满足 ``CitableHit`` 协议，不 import 任何业务模型
（与 ``rerank.RankedHit`` 同样的做法）；cite 出参用本模块的 ``CiteItem`` 值对象，
业务层需要写表/出参时再映射到自己的 Schema。
"""

import re
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.config.setting import settings

from .rerank import Candidate, RankedHit

# 引用编号：[1] / [12]（最多两位，避免把 [2026] 这类年份误判为引用）
_CITE_RE = re.compile(r"\[(\d{1,2})\]")
# Markdown 噪声（标题井号 / 粗体 / 行内代码 / 列表符号 / 表格分隔线）
_MD_NOISE_RE = re.compile(r"(?m)^\s*#{1,6}\s+|^\s*[-*+]\s+|^\s*\d+\.\s+|\*\*|__|`{1,3}|^\s*\|?[\s:|-]{6,}\|?\s*$|\|")
_SPACE_RE = re.compile(r"\s+")


@runtime_checkable
class CitableHit(RankedHit, Protocol):
    """可生成引用的命中项：在重排字段（章节/正文/分数）之外，再带引用展示所需的归属信息。"""

    chunk_id: str
    doc_id: int | str
    doc_title: str | None
    page_no: int | None
    space_name: str | None
    category_name: str | None


@dataclass(frozen=True, slots=True)
class CiteItem:
    """一条引用快照：编号由列表顺序决定（第 n 条即正文里的 [n]）。"""

    doc_id: int | str
    chunk_id: str
    doc: str
    page: int | None
    snippet: str
    space: str
    cat: str


def build_cites(candidates: list[Candidate[CitableHit]], keywords: list[str] | None = None) -> list[CiteItem]:
    """构建引用快照（顺序与 [n] 编号严格一致）。"""
    cites: list[CiteItem] = []
    for candidate in candidates:
        hit = candidate.hit
        cites.append(
            CiteItem(
                doc_id=hit.doc_id,
                chunk_id=hit.chunk_id,
                doc=hit.doc_title or "未命名文档",
                page=hit.page_no,
                snippet=build_snippet(hit.content, keywords, limit=settings.KB_SNIPPET_CHARS),
                space=hit.space_name or "未知空间",
                cat=hit.category_name or "未分类",
            )
        )
    return cites


def build_snippet(content: str, keywords: list[str] | None = None, limit: int = 160) -> str:
    """取「关键词密度最高」的窗口作为引用片段（关键词为空时退化为前缀）。"""
    text = _SPACE_RE.sub(" ", _MD_NOISE_RE.sub(" ", content or "")).strip()
    if len(text) <= limit:
        return text
    hits = [keyword for keyword in (keywords or []) if keyword]
    if not hits:
        return text[:limit] + "…"
    best_start, best_score = 0, -1
    # 20 字步进滑窗：命中关键词长度加权，取密度最高的窗口
    for start in range(0, len(text) - limit + 1, 20):
        window = text[start : start + limit]
        score = sum(len(keyword) for keyword in hits if keyword in window)
        if score > best_score:
            best_score, best_start = score, start
    snippet = text[best_start : best_start + limit]
    if best_start > 0:
        snippet = "…" + snippet
    if best_start + limit < len(text):
        snippet = snippet + "…"
    return snippet


def sanitize_citations(content: str, cite_count: int) -> tuple[str, list[int]]:
    """校验并清理正文中的引用编号（越界编号摘除，不整段作废）。"""
    invalid: list[int] = []

    def _replace(match: re.Match[str]) -> str:
        number = int(match.group(1))
        if 1 <= number <= cite_count:
            return match.group(0)
        invalid.append(number)
        return ""

    cleaned = _CITE_RE.sub(_replace, content or "")
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned, sorted(set(invalid))


def build_followups(candidates: list[Candidate[CitableHit]], question: str, limit: int | None = None) -> list[str]:
    """规则生成问题拓展（零额外模型调用，且保证库内可答）。

    数据来源限定为「本次命中的章节标题 / 文档标题」，因此天然在库内，
    不需要额外做存在性检索（省一次往返）。
    """
    max_count = settings.KB_FOLLOWUP_COUNT if limit is None else limit
    candidates_text: list[str] = []
    for candidate in candidates:
        leaf = (candidate.hit.section_title or "").split(">")[-1].strip()
        # 章节名 ≤ 12 字，保证「〈章节〉是怎么规定的？」不超 20 字
        if leaf and len(leaf) <= 12 and leaf not in question:
            candidates_text.append(f"{leaf}是怎么规定的？")
    if len(candidates_text) < max_count:
        for candidate in candidates:
            title = (candidate.hit.doc_title or "").strip()
            # 标题 ≤ 9 字，保证「《标题》还有哪些要求？」不超 20 字
            if title and len(title) <= 9 and title not in question and title not in (candidate.hit.section_title or ""):
                candidates_text.append(f"《{title}》还有哪些要求？")
            if len(candidates_text) >= max_count:
                break
    return _unique(candidates_text)[:max_count]


def _unique(items: list[str]) -> list[str]:
    """去重保序并过滤超长项。"""
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        value = item.strip()
        if not value or len(value) > 20 or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
