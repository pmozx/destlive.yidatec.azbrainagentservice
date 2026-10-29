"""POC 数据访问层：临时表建表、解析任务、幂等回执、发布写入、关键词/向量召回、耗时记录。

事务口径：所有函数都接收调用方传入的 `db`（请求级会话走 `db_getter`）；
解析任务在后台执行，由 `parser.py` 自己开独立会话，避免复用已随响应关闭的请求事务。

一致性取舍（POC 简化，正式实现见 T1.3/T3.3）：
1. 幂等用「先查后插」而非 UPSERT：POC 单人串行跑，重复插入概率可忽略；
   正式版由唯一键 + ON CONFLICT 保证原子性；
2. 发布写入 = 覆盖该 publication 的全部分片（先删后插，同事务）：契约要求"整体替换"，
   这样 POC 只需首发布路径，修订语义留到 T3.2；
3. 检索不做权限过滤（T4.3 才接 7 张共享表），因此 POC 结果集 = 全库可见内容，绝不可上生产。
"""

import re
from collections.abc import Sequence
from typing import Any, Final

from fastapi import status
from sqlalchemy import delete, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import DatabaseTypeEnum
from app.config.setting import settings
from app.core.database import async_engine
from app.core.logger import logger

from .errors import IDEMPOTENCY_CONFLICT, INTERNAL_ERROR, PocApiError
from .models import (
    CHUNK_EMBEDDING_INDEX,
    CHUNK_TABLE,
    CHAT_MESSAGE_TABLE,
    IDEMPOTENCY_TABLE,
    METRIC_TABLE,
    PARSE_JOB_TABLE,
    PUBLICATION_TABLE,
    PocChatMessageModel,
    PocChunkModel,
    PocIdempotencyModel,
    PocMetricModel,
    PocParseJobModel,
    PocPublicationModel,
)

# 关键词模式上限：每条模式一个 ILIKE 条件，太多会把 SQL 拉长而没有召回收益
_MAX_KEYWORD_PATTERNS: Final[int] = 12
_ASCII_TOKEN = re.compile(r"[A-Za-z0-9_]{2,}")
_CJK_BLOCK = re.compile(r"[\u4e00-\u9fff]+")


def poc_store_ready() -> bool:
    """POC 存储依赖 PostgreSQL（JSONB + pgvector）。"""
    return settings.DATABASE_TYPE == DatabaseTypeEnum.POSTGRES.value


def require_poc_store() -> None:
    """确认 POC 存储可用，否则回 503 让调用方明确「跑不了」而不是静默降级。"""
    if not poc_store_ready():
        raise PocApiError(
            INTERNAL_ERROR,
            f"POC 存储依赖 PostgreSQL，当前 DATABASE_TYPE={settings.DATABASE_TYPE}",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            retryable=True,
        )


async def ensure_poc_tables() -> None:
    """幂等建 POC 临时表与 HNSW 索引（启动期调用，失败只告警）。"""
    if not poc_store_ready():
        logger.warning("POC 临时表跳过创建：DATABASE_TYPE={} 非 postgres", settings.DATABASE_TYPE)
        return

    async with async_engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        for model in (
            PocParseJobModel,
            PocPublicationModel,
            PocChunkModel,
            PocIdempotencyModel,
            PocChatMessageModel,
            PocMetricModel,
        ):
            await conn.run_sync(model.__table__.create, checkfirst=True)
        await conn.execute(
            text(
                f"CREATE INDEX IF NOT EXISTS {CHUNK_EMBEDDING_INDEX} ON {CHUNK_TABLE} "
                "USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
            )
        )
    logger.info(
        "==== POC 临时表就绪：{} / {} / {} / {} / {} / {} ====",
        PARSE_JOB_TABLE,
        PUBLICATION_TABLE,
        CHUNK_TABLE,
        IDEMPOTENCY_TABLE,
        CHAT_MESSAGE_TABLE,
        METRIC_TABLE,
    )


# ── 幂等回执 ──────────────────────────────────────────────────────────


async def get_idempotency(db: AsyncSession, scope: str, key: str) -> tuple[str, dict[str, Any]] | None:
    """取已有回执：返回 (首次正文字摘要, 回执原文)，无记录返回 None。"""
    row = (
        await db.execute(
            select(PocIdempotencyModel.body_hash, PocIdempotencyModel.response).where(
                PocIdempotencyModel.scope == scope, PocIdempotencyModel.key == key
            )
        )
    ).first()
    return (row[0], dict(row[1] or {})) if row else None


async def ensure_idempotent(
    db: AsyncSession,
    *,
    scope: str,
    key: str,
    body_hash: str,
) -> dict[str, Any] | None:
    """同 key 同正文 → 返回原回执（幂等）；同 key 异正文 → 409 `idempotency_conflict`；无记录 → None。"""
    existing = await get_idempotency(db, scope, key)
    if existing is None:
        return None
    stored_hash, response = existing
    if stored_hash != body_hash:
        raise PocApiError(
            IDEMPOTENCY_CONFLICT,
            f"相同标识 {key} 已用于不同的请求正文，请改用新的请求 ID。",
            status_code=status.HTTP_409_CONFLICT,
        )
    return response


async def put_idempotency(db: AsyncSession, *, scope: str, key: str, body_hash: str, response: dict[str, Any]) -> None:
    """登记回执（与业务写入同事务）。

    用 SAVEPOINT 包住插入：并发重复登记触发唯一键冲突时只回退这一条语句，
    不能 `rollback()` 整个请求事务 —— 那会把同一请求里的业务写入一起丢掉。
    """
    try:
        async with db.begin_nested():
            db.add(PocIdempotencyModel(scope=scope, key=key, body_hash=body_hash, response=response))
    except IntegrityError:
        # 回执已存在即视为成功（调用方的 ensure_idempotent 已先挡过一遍）
        logger.warning("POC 幂等回执重复登记：scope={} key={}", scope, key)


# ── 解析任务 ──────────────────────────────────────────────────────────


async def insert_job(
    db: AsyncSession,
    *,
    job_id: str,
    body_hash: str,
    request_body: dict[str, Any],
) -> None:
    db.add(
        PocParseJobModel(
            job_id=job_id,
            body_hash=body_hash,
            request_body=request_body,
            status="queued",
            progress=0,
        )
    )
    await db.flush()


async def get_job(db: AsyncSession, job_id: str) -> dict[str, Any] | None:
    row = (
        await db.execute(
            select(
                PocParseJobModel.job_id,
                PocParseJobModel.body_hash,
                PocParseJobModel.status,
                PocParseJobModel.progress,
                PocParseJobModel.result,
                PocParseJobModel.error_code,
                PocParseJobModel.error_message,
                PocParseJobModel.request_body,
            ).where(PocParseJobModel.job_id == job_id)
        )
    ).first()
    if row is None:
        return None
    return {
        "job_id": row[0],
        "body_hash": row[1],
        "status": row[2],
        "progress": int(row[3] or 0),
        "result": dict(row[4]) if row[4] else None,
        "error_code": row[5],
        "error_message": row[6],
        "request_body": dict(row[7] or {}),
    }


async def update_job(
    db: AsyncSession,
    job_id: str,
    *,
    status: str | None = None,
    progress: int | None = None,
    result: dict[str, Any] | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    """按字段更新任务状态（None 表示不改）；`failed` 只写错误码，HTTP 仍是 200。"""
    values: dict[str, Any] = {}
    if status is not None:
        values["status"] = status
    if progress is not None:
        values["progress"] = progress
    if result is not None:
        values["result"] = result
    if error_code is not None:
        values["error_code"] = error_code
    if error_message is not None:
        values["error_message"] = error_message
    if not values:
        return

    await db.execute(update(PocParseJobModel).where(PocParseJobModel.job_id == job_id).values(**values))


# ── 发布写入 ──────────────────────────────────────────────────────────


async def get_publication(db: AsyncSession, publication_id: str) -> dict[str, Any] | None:
    row = (
        await db.execute(
            select(
                PocPublicationModel.publication_id,
                PocPublicationModel.record_id,
                PocPublicationModel.revision,
                PocPublicationModel.version_id,
                PocPublicationModel.chunk_count,
            ).where(PocPublicationModel.publication_id == publication_id)
        )
    ).first()
    if row is None:
        return None
    return {
        "publication_id": row[0],
        "record_id": row[1],
        "revision": int(row[2]),
        "version_id": row[3],
        "chunk_count": int(row[4]),
    }


async def insert_publication(db: AsyncSession, values: dict[str, Any]) -> None:
    """首发布登记发布单元；`revision` 必须递增（同 publication 已存在由路由层先挡 409）。"""
    db.add(PocPublicationModel(**values))
    await db.flush()


async def replace_chunks(db: AsyncSession, publication_id: str, rows: Sequence[dict[str, Any]]) -> int:
    """整体替换某发布单元的切片集合（先删后插），返回写入条数。"""
    await db.execute(delete(PocChunkModel).where(PocChunkModel.publication_id == publication_id))
    if not rows:
        return 0
    db.add_all([PocChunkModel(publication_id=publication_id, **row) for row in rows])
    await db.flush()
    return len(rows)


# ── 检索 ─────────────────────────────────────────────────────────────


def _escape_like(value: str) -> str:
    """ILIKE 转义：查询词里的 % / _ / \\ 必须当字面量。"""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def keyword_patterns(query: str) -> list[str]:
    """把查询词拆成「整句 + 拉丁词 + 中文二元组」，中文没有分词器时的最省事近似。"""
    text = query.strip()
    patterns: list[str] = []
    if text:
        patterns.append(text)
    patterns.extend(_ASCII_TOKEN.findall(text))
    for block in _CJK_BLOCK.findall(text):
        if len(block) == 1:
            patterns.append(block)
            continue
        patterns.extend(block[index : index + 2] for index in range(len(block) - 1))

    deduped: list[str] = []
    for pattern in patterns:
        if pattern and pattern not in deduped:
            deduped.append(pattern)
    return deduped[:_MAX_KEYWORD_PATTERNS]


def _hit_payload(row: Any, *, score: float, snippet_chars: int) -> dict[str, Any]:
    """把切片行转成契约 hit 结构（#07 与 #14 的上下文共用）。"""
    content = row.content or ""
    snippet = content if len(content) <= snippet_chars else f"{content[:snippet_chars]}……"
    return {
        "resource_type": row.resource_type,
        "resource_id": row.resource_id,
        "publication_id": row.publication_id,
        "record_id": row.record_id,
        "version_id": row.version_id,
        "revision": int(row.revision),
        "knowledge_no": row.knowledge_no,
        "chunk_id": row.chunk_id,
        "title": row.title,
        "content": content,
        "snippet": snippet,
        "page_from": row.page_from,
        "page_to": row.page_to,
        "score": round(max(0.0, min(1.0, score)), 6),
    }


async def vector_search(db: AsyncSession, embedding: Sequence[float], *, limit: int) -> list[dict[str, Any]]:
    """余弦相似度召回（HNSW 索引），score = 1 - 距离，截断到 [0,1]。"""
    if limit <= 0:
        return []
    distance = PocChunkModel.embedding.cosine_distance(list(embedding)).label("distance")
    stmt = (
        select(PocChunkModel, distance)
        .order_by(distance)
        .limit(limit)
    )
    result = await db.execute(stmt)
    hits: list[dict[str, Any]] = []
    for row, dist in result.all():
        hits.append(_hit_payload(row, score=1.0 - float(dist), snippet_chars=settings.KB_SNIPPET_CHARS))
    return hits


async def keyword_search(db: AsyncSession, query: str, *, limit: int) -> list[dict[str, Any]]:
    """关键词召回：ILIKE 粗筛 + 应用侧按命中模式占比打分（不依赖中文分词器）。"""
    patterns = keyword_patterns(query)
    if not patterns or limit <= 0:
        return []

    conditions = [
        PocChunkModel.content.ilike(f"%{_escape_like(pattern)}%", escape="\\") for pattern in patterns
    ]
    # 候选窗口放大 5 倍：应用侧还要按命中模式数重排
    stmt = select(PocChunkModel).where(or_(*conditions)).limit(max(limit * 5, 50))
    result = await db.execute(stmt)

    lowered = [pattern.lower() for pattern in patterns]
    full = lowered[0]
    scored: list[dict[str, Any]] = []
    for row in result.scalars().all():
        content = (row.content or "").lower()
        matched = sum(1 for pattern in lowered if pattern in content)
        if not matched:
            continue
        score = matched / len(lowered)
        if full in content:
            score = max(score, 0.9)
        scored.append(_hit_payload(row, score=score, snippet_chars=settings.KB_SNIPPET_CHARS))

    scored.sort(key=lambda item: item["score"], reverse=True)
    return scored[:limit]


# ── 会话消息与耗时 ────────────────────────────────────────────────────


async def insert_chat_message(
    db: AsyncSession,
    *,
    session_id: str,
    message_id: str,
    user_id: str,
    role: str,
    content: str,
    request_id: str,
) -> None:
    db.add(
        PocChatMessageModel(
            session_id=session_id,
            message_id=message_id,
            user_id=user_id,
            role=role,
            content=content,
            request_id=request_id,
        )
    )
    await db.flush()


async def record_metric(db: AsyncSession, *, kind: str, key: str, total_ms: int, stages: dict[str, Any]) -> None:
    """记一次阶段耗时；失败只告警 —— 计时不准不能拖垮业务请求。"""
    try:
        db.add(PocMetricModel(kind=kind, key=key, total_ms=total_ms, stages=stages))
        await db.flush()
    except Exception:
        logger.warning("POC 阶段耗时记录失败：kind={} key={}", kind, key, exc_info=True)
