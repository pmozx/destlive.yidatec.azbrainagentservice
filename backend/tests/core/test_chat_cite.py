"""引用编号后处理（`app.core.chat.cite`）回归。

引用**快照**（`build_cites` / `build_snippet` / `build_followups`）已随规范 T3 删除：生产侧
引用条目由 ``ChatEvidence.to_citation()`` 直接投影成契约八元组，检索接口也返回完整切片，
那几个函数在生产链路上零调用。这里只钉住仍在使用的越界编号摘除。

覆盖口径：
1. 正文越界编号（模型凭空编造 `[9]`）必须被摘除，且仍保留正文，不整段作废；
2. 没标注任何编号时正文原样返回，不凭空生成引用。
"""

from app.core.chat.cite import sanitize_citations


def test_sanitize_citations_drops_out_of_range_marks() -> None:
    """越界编号摘除并回传，合法编号保留、正文不整段作废。"""
    cleaned, invalid = sanitize_citations("第一句 [1]，第二句 [2]，编造的 [9]。", cite_count=2)

    assert invalid == [9]
    assert "[1]" in cleaned and "[2]" in cleaned
    assert "[9]" not in cleaned
    assert "第一句" in cleaned and "编造的" in cleaned


def test_sanitize_citations_keeps_answer_without_marks() -> None:
    """模型没标任何编号时正文原样返回。"""
    content = "没有标注来源的回答。"

    assert sanitize_citations(content, cite_count=2) == (content, [])


def test_sanitize_citations_dedupes_invalid_numbers() -> None:
    """同一个越界编号出现多次只报一次：它是「模型编了哪个号」的事实，不是出现次数。"""
    cleaned, invalid = sanitize_citations("[5] 与 [5] 都不存在。", cite_count=3)

    assert invalid == [5]
    assert "[5]" not in cleaned
