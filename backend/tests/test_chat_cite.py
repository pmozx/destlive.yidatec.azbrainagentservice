"""引用快照与引用编号后处理（`integration_chat.cite`）回归。

POC 下线（阶段 0）时由 `test_poc_signature.py` 的 citation 用例迁来：
POC 期的 `build_citations/pick_quote` 对应生产侧的 `build_cites/build_snippet/sanitize_citations`。

覆盖口径：
1. cites 顺序与 `[n]` 编号严格一致（第 n 条引用必须是第 n 个候选，不能重排）；
2. snippet 取「关键词密度最高」的窗口，不是简单截断前 N 字；
3. 正文越界编号（模型凭空编造 `[9]`）必须被摘除，且仍保留正文，不整段作废。
"""

from app.config.setting import settings
from app.modules.integration_chat.cite import build_cites, build_snippet, sanitize_citations
from app.modules.integration_chat.rerank import Candidate
from app.modules.integration_kb.retrieval import KnowledgeHit


def _hit(index: int, content: str) -> KnowledgeHit:
    return KnowledgeHit(
        chunk_id=index,
        space_id=1,
        doc_id=1,
        chunk_index=index,
        page_no=1,
        section_title="申请流程",
        content=content,
        score=0.9,
        doc_title="VPN 申请操作手册",
        file_name="vpn-guide.md",
        file_type="md",
        space_name="运维空间",
        category_name="操作手册",
        meta={},
    )


def test_build_cites_keeps_candidate_order() -> None:
    """cites 顺序与候选顺序一致：模型正文里的 [1] 必须仍然指第 1 个候选。"""
    candidates = [Candidate(hit=_hit(1, "在服务台提交 vpn 申请。"), vector_score=0.9),
                  Candidate(hit=_hit(2, "需要负责人审核。"), vector_score=0.8)]
    cites = build_cites(candidates, keywords=["vpn"])

    assert [item.chunk_id for item in cites] == [1, 2]
    assert [item.doc_id for item in cites] == [1, 1]
    assert cites[0].doc == "VPN 申请操作手册"
    assert cites[0].page == 1
    for item, candidate in zip(cites, candidates, strict=True):
        assert item.snippet in candidate.hit.content


def test_snippet_prefers_keyword_window_over_head() -> None:
    """长正文里取关键词命中的窗口，而不是无脑截取开头。"""
    filler = "无关内容。" * 40
    content = f"{filler}在服务台提交 vpn 申请，由负责人审核后开通账号。{filler}"
    snippet = build_snippet(content, keywords=["vpn"], limit=settings.KB_SNIPPET_CHARS)

    assert "vpn" in snippet
    assert len(snippet) <= settings.KB_SNIPPET_CHARS + 2  # 两端各可能补一个省略号


def test_sanitize_citations_drops_out_of_range_marks() -> None:
    """越界编号摘除并回传，合法编号保留、正文不整段作废。"""
    cleaned, invalid = sanitize_citations("第一句 [1]，第二句 [2]，编造的 [9]。", cite_count=2)

    assert invalid == [9]
    assert "[1]" in cleaned and "[2]" in cleaned
    assert "[9]" not in cleaned
    assert "第一句" in cleaned and "编造的" in cleaned


def test_sanitize_citations_keeps_answer_without_marks() -> None:
    """模型没标任何编号时正文原样返回，不凭空生成引用。"""
    content = "没有标注来源的回答。"
    cleaned, invalid = sanitize_citations(content, cite_count=2)

    assert cleaned == content
    assert invalid == []
    assert build_cites([], keywords=["vpn"]) == []
