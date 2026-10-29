"""#07 / #14 的检索层：关键词、向量、RRF 融合。

口径：
1. `mode=keyword` 只走关键词（T4.1 的正式实现会换成全文/trigram + 打分），
   `mode=natural_language` 走「向量召回 + 关键词召回 → RRF 融合」（T0.3 验证的目标链路）；
2. RRF 用名次而不是分数：向量余弦与关键词占比的分布完全不同，直接加权会把一路彻底压死；
   `score = Σ 1/(k + rank)`，融合后再线性归一到 [0,1]，满足契约「score 为 0–1 相关度」；
3. POC **不做权限过滤**（T4.3 才接 GSI 共享表），所以这里召回的候选集是全库，
   返回值绝不可以直接当成"用户可见内容"，只在 POC 自签环境下使用。

契约只要求 `score` 落在 0–1，没规定量化口径 —— 归一化方式已登记在 T0.4 契约疑点清单。
"""

from collections.abc import Sequence
from typing import Any, Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.config.setting import settings
from app.core.clients.embedding import EmbeddingClient

from . import store
from .metrics import StageTimer

# #07 响应允许暴露的字段（`content` 只给生成用，不直接回给 GSI，避免整段正文出现在列表接口）
PUBLIC_HIT_FIELDS: Final[tuple[str, ...]] = (
    "resource_type",
    "resource_id",
    "publication_id",
    "record_id",
    "version_id",
    "revision",
    "knowledge_no",
    "chunk_id",
    "title",
    "snippet",
    "page_from",
    "page_to",
    "score",
)


def public_hit(hit: dict[str, Any]) -> dict[str, Any]:
    """按契约投影命中字段（None 的可选键省略）。"""
    return {field: hit[field] for field in PUBLIC_HIT_FIELDS if hit.get(field) is not None}


def rrf_fuse(hit_lists: Sequence[list[dict[str, Any]]], *, k: int, limit: int) -> list[dict[str, Any]]:
    """Reciprocal Rank Fusion：按名次累加，融合后归一化到 [0,1] 并截断。"""
    scores: dict[str, float] = {}
    payloads: dict[str, dict[str, Any]] = {}
    for hits in hit_lists:
        for rank, hit in enumerate(hits, start=1):
            key = str(hit["chunk_id"])
            payloads.setdefault(key, hit)
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)

    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)[:limit]
    if not ranked:
        return []
    top_score = ranked[0][1]
    fused: list[dict[str, Any]] = []
    for key, score in ranked:
        hit = dict(payloads[key])
        hit["score"] = round(score / top_score, 6) if top_score else 0.0
        fused.append(hit)
    return fused


async def retrieve(
    db: AsyncSession,
    *,
    query: str,
    mode: str,
    limit: int,
    timer: StageTimer | None = None,
) -> list[dict[str, Any]]:
    """按模式召回，返回内部命中结构（含 `content`，供 #14 拼上下文）。"""
    timer = timer or StageTimer()
    if limit <= 0:
        return []

    if mode == "keyword":
        async with timer.stage("keyword"):
            return await store.keyword_search(db, query, limit=limit)

    top_k = max(settings.POC_RETRIEVE_TOP_K, limit)
    async with timer.stage("embed"):
        embedding = await EmbeddingClient().embed_query(query)
    async with timer.stage("vector"):
        vector_hits = await store.vector_search(db, embedding, limit=top_k)
    async with timer.stage("keyword"):
        keyword_hits = await store.keyword_search(db, query, limit=top_k)
    async with timer.stage("fuse"):
        return rrf_fuse([vector_hits, keyword_hits], k=settings.POC_RRF_K, limit=max(limit, 1))


async def retrieve_for_answer(
    db: AsyncSession,
    *,
    question: str,
    timer: StageTimer | None = None,
) -> list[dict[str, Any]]:
    """#14 的检索入口：自然语言模式 + 融合后只保留进入上下文的条数。"""
    return await retrieve(
        db,
        query=question,
        mode="natural_language",
        limit=settings.POC_RETRIEVE_TOP_N,
        timer=timer,
    )
