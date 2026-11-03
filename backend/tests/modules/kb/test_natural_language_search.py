"""#07 自然语言检索回归（T4.2）：语义召回 / RRF 融合 / 精排 / 依赖故障。

验收断言：
1. 语义命中：正文里没有字面关键词，也能靠向量召回（关键词通道 0 条时结果仍正确）；
2. 授权与生效指针仍是硬过滤：非成员团队、旧 ``record_id`` 的切片一条都不返回；
3. 余弦相似度低于 ``KB_RETRIEVE_MIN_SCORE`` 的切片不进候选，全部落空时
   ``no_evidence=true``（不是报错、也不是伪造命中）；
4. 精排可用时用重排序相关度做 ``score`` 并按它排序（服务返回 logit，先在 ``core.chat.rerank``
   经 sigmoid 压到 0–1，闸门也只在 0–1 空间比较）；归一后低于
   ``max(KB_RERANK_MIN_SCORE, top1 × KB_RERANK_RELATIVE_RATIO)`` 的命中被丢弃；精排不可用
   时回落 RRF 融合序，接口照常 200；
5. 向量化依赖不可用 → 依赖故障（不静默退化成纯关键词检索）。

外部依赖全部打桩：查询向量固定为 ``e0``，精排默认不可用（走融合降级路径）。
真实 bge-m3 / bge-reranker 的联调用 ``app/scripts/contract/mock_gsi.py``。
"""

import uuid
from typing import Any

import pytest

from app.config.setting import settings
from app.core.clients import EmbeddingClient, RerankClient, RerankHit
from app.core.exceptions import CustomException
from app.modules.kb.retrieval.crud import ChunkCandidate
from app.modules.kb.retrieval.service import _rerank, _rrf_fuse
from app.modules_api.contract import errors as contract_errors

from tests.support import requires_db

from tests.modules.kb.search_support import (
    KNOWLEDGE_NO,
    TITLE,
    USER_ID,
    cleanup as _cleanup,
    entry as _entry,
    gsi_publication as _gsi_publication,
    publication as _publication,
    ready as _ready,
    search as _search,
    search_payload as _search_payload,
    snapshot as _snapshot,
    team as _team,
)


def _vector(*, dim: int = 0) -> list[float]:
    """单位向量：第 ``dim`` 维为 1，其余 0（余弦相似度只由方向决定，便于精确摆分数）。"""
    vector = [0.0] * settings.VECTOR_DIM
    vector[dim] = 1.0
    return vector


@pytest.fixture(autouse=True)
def _stub_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    """查询向量固定为 e0；精排默认按「服务不可用」处理（走 RRF 降级序）。"""

    async def fake_embed_query(self: EmbeddingClient, text: str) -> list[float]:
        return _vector(dim=0)

    async def unavailable_rerank(self: RerankClient, query: str, documents: Any, *, top_n: int | None = None, model: str | None = None):
        raise CustomException(msg="精排服务不可用（测试桩）", status_code=503)

    monkeypatch.setattr(EmbeddingClient, "embed_query", fake_embed_query)
    monkeypatch.setattr(RerankClient, "rerank", unavailable_rerank)


def _candidate(seq: int, *, publication_id: str = "pub-rrf", vector_score: float | None = None) -> ChunkCandidate:
    """RRF 纯函数用例的候选（同一快照内的不同切片）。"""
    return ChunkCandidate(
        publication_id=publication_id,
        resource_type="document",
        resource_id=f"doc-{publication_id}",
        record_id="record-rrf",
        version_id="1",
        knowledge_no=KNOWLEDGE_NO,
        revision=101,
        title=TITLE,
        chunk_id=f"record-rrf:{seq}",
        seq=seq,
        heading=None,
        content=f"正文 {seq}",
        page_from=None,
        page_to=None,
        vector_score=vector_score,
    )


# ── 纯函数：RRF 融合（精排分归一化在 tests/core/test_chat_rerank.py）─────────────────────


def test_rrf_fuse_rewards_hits_from_both_channels() -> None:
    """双通道都命中的切片拿两份分排第一；归一后 top1 恒为 1.0，其余严格递减。"""
    first, second, third = _candidate(1), _candidate(2), _candidate(3)
    fused = _rrf_fuse([first, second], [second, third])

    assert [candidate.seq for _, candidate in fused] == [2, 1, 3]
    assert fused[0][0] == 1.0
    assert fused[1][0] > fused[2][0]
    assert all(0.0 < score <= 1.0 for score, _ in fused)


def test_rrf_fuse_handles_single_empty_channel() -> None:
    """一路为空时按另一路的排名输出，不报错也不把分数拉满到不该有的位置。"""
    first, second = _candidate(1, vector_score=0.9), _candidate(2, vector_score=0.8)
    fused = _rrf_fuse([first, second], [])

    assert [candidate.seq for _, candidate in fused] == [1, 2]
    assert fused[0][0] == 1.0
    assert _rrf_fuse([], []) == []


# ── 纯函数：精排窗口（同时是返回条数上限）───────────────────────────


async def test_rerank_window_caps_documents_and_returned_hits(monkeypatch: pytest.MonkeyPatch) -> None:
    """窗口既限定送精排的条数，也限定返回条数：精排分与融合分不同源，不能混排。

    精排耗时与条数严格线性（CPU 实测 ~1s/条），窗口超出预算只会把尾段预算耗完再降级；
    而把没打分的尾部候选补进结果，会让 ``score`` 一半是精排相关度、一半是 RRF 相对分。
    """
    monkeypatch.setattr(settings, "SEARCH_RERANK_WINDOW", 3)
    seen: list[str] = []

    async def fake_rerank(
        self: RerankClient, query: str, documents: Any, *, top_n: int | None = None, model: str | None = None
    ) -> list[RerankHit]:
        seen.extend(documents)
        return [
            RerankHit(index=index, score=2.4 - index, document=document)
            for index, document in enumerate(documents)
        ]

    monkeypatch.setattr(RerankClient, "rerank", fake_rerank)
    fused = [(round(1.0 - index * 0.05, 4), _candidate(index + 1)) for index in range(8)]

    result = await _rerank("语义检索问题", fused, limit=10)

    assert len(seen) == 3  # 只送窗口内的候选
    assert [candidate.seq for _, candidate in result] == [1, 2, 3]  # 尾部 5 条不补进结果


# ── 接口回归 ─────────────────────────────────────────────────────────


@requires_db
async def test_natural_language_recalls_semantically_without_keyword_hit() -> None:
    """正文里没有「vpn」，靠向量召回也能命中；不相关切片（距离过大）被阈值挡掉。"""
    await _ready()
    team_id, group_code = await _team(members=[USER_ID])
    publication_id, record_id = await _entry(
        team=group_code,
        chunks=[
            {
                "seq": 1,
                "heading": "远程接入",
                "content": "开通专线账号需要先在服务台提交工单。",
                "embedding": _vector(dim=0),
            },
            {"seq": 2, "content": "食堂本周菜单与用餐时间。", "embedding": _vector(dim=1)},
        ],
    )
    try:
        response = await _search(_search_payload(query="怎么办理 vpn", mode="natural_language"))
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["returned_count"] == len(body["hits"]) == 1
        assert body["no_evidence"] is False

        hit = body["hits"][0]
        assert hit["chunk_id"] == f"{record_id}:1"
        assert hit["publication_id"] == publication_id
        assert 0.0 < hit["score"] <= 1.0
        # 纯语义命中：摘要回落切片开头，仍然只来自本切片正文
        assert hit["snippet"].startswith("开通专线账号")
    finally:
        await _cleanup([publication_id], [team_id])


@requires_db
async def test_natural_language_is_scoped_to_audience_team() -> None:
    """授权下推：向量完全相同的非成员团队切片也不返回。"""
    await _ready()
    team_a_id, team_a = await _team(members=[USER_ID])
    team_b_id, team_b = await _team(members=[])
    mine_id, _ = await _entry(
        team=team_a, chunks=[{"seq": 1, "content": "授权内语义正文", "embedding": _vector(dim=0)}]
    )
    other_id, _ = await _entry(
        team=team_b, chunks=[{"seq": 1, "content": "越权语义正文", "embedding": _vector(dim=0)}]
    )
    try:
        response = await _search(_search_payload(query="语义检索问题", mode="natural_language"))
        assert response.status_code == 200, response.text
        assert [hit["publication_id"] for hit in response.json()["hits"]] == [mine_id]
    finally:
        await _cleanup([mine_id, other_id], [team_a_id, team_b_id])


@requires_db
async def test_natural_language_drops_candidates_below_vector_floor() -> None:
    """余弦相似度不足（这里是 0）时没有候选：200 + `no_evidence=true`，不是 500。"""
    await _ready()
    team_id, group_code = await _team(members=[USER_ID])
    publication_id, _ = await _entry(
        team=group_code, chunks=[{"seq": 1, "content": "正交向量的无关正文", "embedding": _vector(dim=1)}]
    )
    try:
        response = await _search(_search_payload(query="怎么办理 vpn", mode="natural_language"))
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["hits"] == []
        assert body["returned_count"] == 0
        assert body["no_evidence"] is True
    finally:
        await _cleanup([publication_id], [team_id])


@requires_db
async def test_natural_language_reads_current_snapshot_only() -> None:
    """生效指针是硬过滤：旧快照切片向量完全匹配也不召回。"""
    await _ready()
    team_id, group_code = await _team(members=[USER_ID])
    publication_id = f"pub-{uuid.uuid4().hex[:12]}"
    old_record = f"record-{uuid.uuid4().hex[:12]}"
    new_record = f"record-{uuid.uuid4().hex[:12]}"
    try:
        await _gsi_publication(publication_id=publication_id, team=group_code, record_id=new_record)
        await _publication(publication_id, current_record_id=new_record, current_revision=102)
        await _snapshot(
            publication_id,
            old_record,
            [{"seq": 1, "content": "历史版本语义正文", "embedding": _vector(dim=0)}],
            revision=101,
        )
        await _snapshot(
            publication_id,
            new_record,
            [{"seq": 1, "content": "当前版本无关正文", "embedding": _vector(dim=1)}],
            revision=102,
        )

        response = await _search(_search_payload(query="语义检索问题", mode="natural_language"))
        assert response.status_code == 200, response.text
        assert response.json()["no_evidence"] is True
    finally:
        await _cleanup([publication_id], [team_id])


@requires_db
async def test_natural_language_uses_rerank_order_and_score(monkeypatch: pytest.MonkeyPatch) -> None:
    """精排可用时按重排序相关度排序，`score` 是 logit 经 sigmoid 后的 0–1 值（覆盖融合序）。"""

    async def fake_rerank(
        self: RerankClient, query: str, documents: Any, *, top_n: int | None = None, model: str | None = None
    ) -> list[RerankHit]:
        # 反转融合序：第二个候选更相关；分值按 llama.cpp 的真实口径给 logit（均高于闸门）
        return [
            RerankHit(index=1, score=2.4, document=documents[1]),
            RerankHit(index=0, score=1.1, document=documents[0]),
        ]

    monkeypatch.setattr(RerankClient, "rerank", fake_rerank)

    await _ready()
    team_id, group_code = await _team(members=[USER_ID])
    publication_id, record_id = await _entry(
        team=group_code,
        chunks=[
            {"seq": 1, "content": "第一条语义正文", "embedding": _vector(dim=0)},
            {"seq": 2, "content": "第二条语义正文", "embedding": _vector(dim=0)},
        ],
    )
    try:
        response = await _search(_search_payload(query="语义检索问题", mode="natural_language"))
        assert response.status_code == 200, response.text
        hits = response.json()["hits"]
        assert [hit["chunk_id"] for hit in hits] == [f"{record_id}:2", f"{record_id}:1"]
        assert [hit["score"] for hit in hits] == [0.9168, 0.7503]
    finally:
        await _cleanup([publication_id], [team_id])


@requires_db
async def test_natural_language_feeds_document_title_and_truncates_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """精排入参 = 文档标题 + 切片小标题 + 截断后的正文（标题不截断）。

    实测缺文档标题时中文 query × 英文正文的 logit 全为负（-0.44 / -2.62 / -3.81），
    整批命中会被判成无证据；补上标题后回到 3.64 / 3.15 / 1.56。正文截断只影响送
    交叉编码器的原文量（耗时），不影响返回给调用方的 ``snippet``。
    """
    marker = "尾部标记不得送入精排"
    seen: list[str] = []

    async def inspect_rerank(
        self: RerankClient, query: str, documents: Any, *, top_n: int | None = None, model: str | None = None
    ) -> list[RerankHit]:
        seen.extend(documents)
        return [RerankHit(index=0, score=2.4, document=documents[0])]

    monkeypatch.setattr(RerankClient, "rerank", inspect_rerank)

    await _ready()
    team_id, group_code = await _team(members=[USER_ID])
    publication_id, _ = await _entry(
        team=group_code,
        chunks=[
            {
                "seq": 1,
                "heading": "远程接入",
                "content": "开篇正文。" + "填充" * 400 + marker,
                "embedding": _vector(dim=0),
            }
        ],
    )
    try:
        response = await _search(_search_payload(query="语义检索问题", mode="natural_language"))
        assert response.status_code == 200, response.text
        assert [hit["score"] for hit in response.json()["hits"]] == [0.9168]
    finally:
        await _cleanup([publication_id], [team_id])

    assert len(seen) == 1
    assert TITLE in seen[0]  # 跨语言锚点：文档标题必须进精排而非只给切片
    assert "远程接入" in seen[0]  # 切片小标题同样保留
    assert seen[0].startswith(TITLE)
    assert "开篇正文。" in seen[0]  # 正文头部保留
    assert marker not in seen[0]  # 超出 SEARCH_RERANK_DOC_CHARS 的尾巴被截掉
    title_len = len(TITLE) + 1 + len("远程接入")
    assert len(seen[0]) == title_len + 1 + settings.SEARCH_RERANK_DOC_CHARS


@requires_db
async def test_natural_language_drops_hits_below_rerank_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    """精排认为都不相关时（logit -3.0 归一后约 0.05，低于绝对下限）返回空结果而不是凑数。"""

    async def low_score_rerank(
        self: RerankClient, query: str, documents: Any, *, top_n: int | None = None, model: str | None = None
    ) -> list[RerankHit]:
        return [RerankHit(index=index, score=-3.0, document=document) for index, document in enumerate(documents)]

    monkeypatch.setattr(RerankClient, "rerank", low_score_rerank)

    await _ready()
    team_id, group_code = await _team(members=[USER_ID])
    publication_id, _ = await _entry(
        team=group_code, chunks=[{"seq": 1, "content": "勉强相关的语义正文", "embedding": _vector(dim=0)}]
    )
    try:
        response = await _search(_search_payload(query="语义检索问题", mode="natural_language"))
        assert response.status_code == 200, response.text
        assert response.json()["no_evidence"] is True
    finally:
        await _cleanup([publication_id], [team_id])


@requires_db
async def test_natural_language_reports_embedding_dependency_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """向量化不可用：按依赖故障报错（可重试），绝不退化成只有字面命中的结果。"""

    async def broken_embed_query(self: EmbeddingClient, text: str) -> list[float]:
        raise CustomException(msg="向量化服务不可用（测试桩）", status_code=503)

    monkeypatch.setattr(EmbeddingClient, "embed_query", broken_embed_query)

    await _ready()
    team_id, group_code = await _team(members=[USER_ID])
    publication_id, _ = await _entry(
        team=group_code, chunks=[{"seq": 1, "content": "vpn 申请正文", "embedding": _vector(dim=0)}]
    )
    try:
        response = await _search(_search_payload(query="vpn 申请", mode="natural_language"))
        assert response.status_code == 500, response.text
        error = response.json()["error"]
        assert error["code"] == contract_errors.INTERNAL_ERROR
        assert error["retryable"] is True
    finally:
        await _cleanup([publication_id], [team_id])


@requires_db
async def test_natural_language_returns_no_evidence_without_team() -> None:
    """无受众团队：授权集合为空 → 空结果 `no_evidence=true`（连向量化都不该被调用）。"""
    await _ready()
    team_id, group_code = await _team(members=[])
    publication_id, _ = await _entry(
        team=group_code, chunks=[{"seq": 1, "content": "无人可见的语义正文", "embedding": _vector(dim=0)}]
    )
    try:
        response = await _search(
            _search_payload(query="语义检索问题", mode="natural_language", user_id="900999")
        )
        assert response.status_code == 200, response.text
        assert response.json()["no_evidence"] is True
    finally:
        await _cleanup([publication_id], [team_id])
