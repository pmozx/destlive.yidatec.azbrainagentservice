"""core.chat.rerank 回归：分数归一化 + 三闸判定（不依赖数据库与网络）。

验收断言：
1. 精排服务返回 logit 时，``Candidate.final_score`` 必须已归一到 0–1（上游只看 0–1）；
2. 归一化是单调的，不改变排序；极端值不得抛 ``OverflowError``；
3. ``RERANK_SCORE_IS_LOGIT=false``（服务返回概率）时按原值使用，不再二次压缩；
4. 三闸（条数 / 绝对分 / 相对分）在 0–1 空间才成立：相对闸门是乘比例，负数会反向。
"""

from dataclasses import dataclass
from typing import Any

import pytest

from app.config.setting import settings
from app.core.chat import (
    Candidate,
    grade,
    normalize_rerank_score,
    rerank_candidates,
    to_candidates,
)
from app.core.clients import RerankClient, RerankHit


@dataclass(frozen=True, slots=True)
class _Hit:
    """满足 ``RankedHit`` 协议的最小命中结构。"""

    section_title: str | None
    content: str
    score: float


# ── 归一化 ───────────────────────────────────────────────────────────


def test_normalize_maps_logit_into_unit_range() -> None:
    """logit（可正可负、可大于 1）必须落到 0–1，且不能把正的高分全压成 1.0。"""
    assert normalize_rerank_score(2.4) == 0.9168
    assert normalize_rerank_score(-0.4) == 0.4013
    assert normalize_rerank_score(0.0) == 0.5
    # 实测样本：带文档标题后 3.64 / 3.15 / 1.56，sigmoid 后仍保留区分度
    scores = [normalize_rerank_score(value) for value in (3.6438, 3.1467, 1.5622, -0.4439)]
    assert scores == sorted(scores, reverse=True)
    assert len(set(scores)) == 4
    assert scores[-1] < 0.5  # 负 logit 应落在 50% 以下，不能与正分混为一谈


def test_normalize_survives_extreme_values() -> None:
    """±1e9 这类异常分值不得抛 exp 溢出，结果仍钳在 0–1。"""
    assert normalize_rerank_score(1e9) == 1.0
    assert normalize_rerank_score(-1e9) == 0.0
    assert 0.0 <= normalize_rerank_score(60.0) <= 1.0
    assert 0.0 <= normalize_rerank_score(-60.0) <= 1.0


def test_normalize_passthrough_when_service_returns_probability(monkeypatch: pytest.MonkeyPatch) -> None:
    """服务已返回 0–1 概率（Jina/Cohere 口径）时不得二次压缩。"""
    monkeypatch.setattr(settings, "RERANK_SCORE_IS_LOGIT", False)
    assert normalize_rerank_score(0.98) == 0.98
    assert normalize_rerank_score(1.7) == 1.0
    assert normalize_rerank_score(-0.2) == 0.0


# ── 精排结果归一 ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rerank_candidates_normalizes_service_scores(monkeypatch: pytest.MonkeyPatch) -> None:
    """服务返回 logit，``Candidate.final_score`` 必须是归一后的 0–1。"""

    async def fake_rerank(
        self: RerankClient, query: str, documents: Any, *, top_n: int | None = None, model: str | None = None
    ) -> list[RerankHit]:
        assert query == "造林对流量历时曲线有什么影响"
        assert documents[0] == "文档标题\n正文一"
        return [
            RerankHit(index=1, score=3.15, document=documents[1]),
            RerankHit(index=0, score=-0.44, document=documents[0]),
        ]

    monkeypatch.setattr(RerankClient, "rerank", fake_rerank)
    candidates = to_candidates(
        [
            _Hit(section_title="文档标题", content="正文一", score=0.9),
            _Hit(section_title=None, content="正文二", score=0.8),
        ]
    )

    ranked, used = await rerank_candidates("造林对流量历时曲线有什么影响", candidates, 2)

    assert used is True
    assert [item.final_score for item in ranked] == [0.9589, 0.3917]
    assert all(0.0 <= item.final_score <= 1.0 for item in ranked)
    # 召回分原样保留，便于排障时对比「精排前 vs 精排后」
    assert [item.vector_score for item in ranked] == [0.8, 0.9]


@pytest.mark.asyncio
async def test_rerank_candidates_degrades_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """精排超时只降级为召回序（used=False），不把异常抛给检索/问答链路。"""

    async def timeout_rerank(
        self: RerankClient, query: str, documents: Any, *, top_n: int | None = None, model: str | None = None
    ) -> list[RerankHit]:
        raise TimeoutError("精排尾段预算用尽（测试桩）")

    monkeypatch.setattr(RerankClient, "rerank", timeout_rerank)
    candidates = to_candidates([_Hit(section_title=None, content="正文", score=0.7)])

    ranked, used = await rerank_candidates("q", candidates, 1, timeout=0.01)

    assert used is False
    assert [item.final_score for item in ranked] == [0.7]


# ── 三闸判定 ─────────────────────────────────────────────────────────


def _candidate(score: float, *, reranked: bool = True) -> Candidate[_Hit]:
    """构造一条已归一的候选（reranked=True 时 final_score 取精排分）。"""
    hit = _Hit(section_title=None, content="正文", score=0.5)
    return Candidate(hit=hit, vector_score=0.5, rerank_score=score if reranked else None)


def test_grade_keeps_tail_close_to_top1() -> None:
    """相对闸门在 0–1 空间按比例裁尾：0.958 / 0.827 都留，0.20 被裁。"""
    result = grade([_candidate(0.958), _candidate(0.827), _candidate(0.20)], reranked=True)

    assert result.reranked is True
    assert [item.final_score for item in result.candidates] == [0.958, 0.827]
    assert result.threshold == settings.KB_RERANK_MIN_SCORE


def test_grade_rejects_when_top1_below_absolute_floor() -> None:
    """最相关的一条都不够格 = 拒答，返回空候选。"""
    result = grade([_candidate(0.22), _candidate(0.21)], reranked=True)

    assert result.candidates == []
    assert result.reason == "low_score"


def test_grade_uses_recall_score_when_rerank_unavailable() -> None:
    """精排不可用时闸门换成召回分下限，与精排阈值互不复用。"""
    result = grade([_candidate(0.5, reranked=False)], reranked=False)

    assert result.threshold == settings.KB_RETRIEVE_MIN_SCORE
    assert [item.final_score for item in result.candidates] == [0.5]
