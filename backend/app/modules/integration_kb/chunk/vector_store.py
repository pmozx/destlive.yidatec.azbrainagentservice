"""pgvector 向量存储（集成-知识库）：建表/索引、幂等写入、余弦相似度检索。

设计约定：
1. 仅 PostgreSQL + pgvector 可用；其它数据库调用即报错，不做静默降级，
   避免「看起来写入成功、实际检索不到」的隐性故障；
2. 一个库只保留一套向量维度（由 VECTOR_DIM 决定），换维度必须重建表，
   ensure 阶段会比对已有表的实际维度并提示；
3. embedding 由调用方生成后传入，本模块不绑定具体模型供应商；维度校验在此收口；
4. 距离度量固定 cosine，检索分数统一换算为 ``1 - cosine_distance``（越大越相似）；
5. 表与扩展的创建是幂等的：initdb 脚本已建过也没关系，重复执行无副作用。

表 ``sbp_ai_integration_kb_chunk`` 的模型见同目录 ``model.py``；HTTP 接口见同目录 ``controller.py``。
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from fastapi import status
from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import DatabaseTypeEnum
from app.config.setting import settings
from app.core.database import async_engine
from app.core.exceptions import CustomException
from app.core.logger import logger

from .model import (
    INTEGRATION_KB_CHUNK_EMBEDDING_INDEX_NAME,
    INTEGRATION_KB_CHUNK_TABLE_NAME,
    IntegrationKbChunkModel,
)


def vector_store_ready() -> bool:
    """当前配置是否具备向量能力（postgres 且开关打开）。"""
    return settings.VECTOR_ENABLE and settings.DATABASE_TYPE == DatabaseTypeEnum.POSTGRES.value


def _require_postgres() -> None:
    """向量能力不可用时抛出可读异常（而不是让 asyncpg 抛扩展不存在）。"""
    if settings.DATABASE_TYPE != DatabaseTypeEnum.POSTGRES.value:
        raise CustomException(
            msg=f"向量存储依赖 PostgreSQL + pgvector，当前 DATABASE_TYPE={settings.DATABASE_TYPE}",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    if not settings.VECTOR_ENABLE:
        raise CustomException(
            msg="向量存储已关闭（VECTOR_ENABLE=false）",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )


def validate_embedding(embedding: Sequence[float]) -> list[float]:
    """校验单个向量维度与 VECTOR_DIM 一致，返回可写入的 float 列表。"""
    if len(embedding) != settings.VECTOR_DIM:
        raise CustomException(
            msg=f"向量维度不匹配：期望 {settings.VECTOR_DIM}，实际 {len(embedding)}（请核对 embedding 模型与 VECTOR_DIM）",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    return [float(value) for value in embedding]


async def _existing_vector_dim(conn) -> int | None:
    """读取已有分片表 embedding 列的实际维度（表不存在时返回 None）。"""
    result = await conn.execute(
        text(
            "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
            "WHERE attrelid = to_regclass(:table_name) AND attname = 'embedding' AND attnum > 0"
        ),
        {"table_name": INTEGRATION_KB_CHUNK_TABLE_NAME},
    )
    row = result.first()
    if row is None or not row[0]:
        return None
    # format_type 返回形如 vector(1024)；未声明维度时为 vector
    spec = str(row[0])
    if "(" not in spec:
        return None
    return int(spec.split("(")[1].rstrip(")"))


async def ensure_vector_store() -> None:
    """幂等创建 vector 扩展、分片表与 HNSW 索引（应用启动时调用）。

    HNSW + vector_cosine_ops 与检索口径（<=> 余弦距离）保持一致；索引参数 m/ef_construction
    取 pgvector 默认值，数据量增长后可按召回率单独调整。
    """
    if not (vector_store_ready() and settings.VECTOR_AUTO_CREATE):
        return

    async with async_engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(IntegrationKbChunkModel.__table__.create, checkfirst=True)
        existing_dim = await _existing_vector_dim(conn)
        if existing_dim is not None and existing_dim != settings.VECTOR_DIM:
            raise CustomException(
                msg=(
                    f"分片表 {INTEGRATION_KB_CHUNK_TABLE_NAME} 的向量维度为 {existing_dim}，与 VECTOR_DIM={settings.VECTOR_DIM} 不一致；"
                    "换维度无法原地修改，需重建表并重新灌入向量"
                )
            )
        await conn.execute(
            text(
                f"CREATE INDEX IF NOT EXISTS {INTEGRATION_KB_CHUNK_EMBEDDING_INDEX_NAME} ON {INTEGRATION_KB_CHUNK_TABLE_NAME} "
                "USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
            )
        )

    logger.info(
        "==== 向量存储就绪（表 {}，维度 {}，索引 HNSW/cosine）====",
        INTEGRATION_KB_CHUNK_TABLE_NAME,
        settings.VECTOR_DIM,
    )


async def upsert_chunks(session: AsyncSession, chunks: Sequence[dict[str, Any]]) -> int:
    """按 (space_id, doc_id, chunk_index) 幂等写入分片，返回写入条数。

    每个元素需包含：space_id、doc_id、chunk_index、content、embedding；
    可选 page_no、section_title、token_count、meta。同一文档重跑向量化不会产生重复分片；
    重跑后条数变少时会清掉多出来的尾块 —— 否则旧 chunk_index 永不更新，
    会一直带着上一次（可能是空页码）的内容出现在分页查询里。
    """
    _require_postgres()
    if not chunks:
        return 0

    now = datetime.now(UTC)
    rows: list[dict[str, Any]] = []
    for item in chunks:
        content = item["content"]
        rows.append(
            {
                "space_id": item["space_id"],
                "doc_id": item["doc_id"],
                "chunk_index": int(item["chunk_index"]),
                "content": content,
                "page_no": item.get("page_no"),
                "section_title": item.get("section_title"),
                "token_count": int(item.get("token_count") or len(content)),
                "meta": item.get("meta") or {},
                "embedding": validate_embedding(item["embedding"]),
                "created_time": now,
                "updated_time": now,
            }
        )

    stmt = insert(IntegrationKbChunkModel).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=[
            IntegrationKbChunkModel.space_id,
            IntegrationKbChunkModel.doc_id,
            IntegrationKbChunkModel.chunk_index,
        ],
        set_={
            "content": stmt.excluded.content,
            "page_no": stmt.excluded.page_no,
            "section_title": stmt.excluded.section_title,
            "token_count": stmt.excluded.token_count,
            "meta": stmt.excluded.meta,
            "embedding": stmt.excluded.embedding,
            # 用数据库时间，避免多实例应用时钟漂移导致更新时间回退
            "updated_time": func.now(),
        },
    )
    await session.execute(stmt)
    # 按文档统计本次的分片上界，删掉超出范围的旧尾块（同一批可能包含多个文档）
    upper_bounds: dict[tuple[int, int], int] = {}
    for item in rows:
        key = (item["space_id"], item["doc_id"])
        upper_bounds[key] = max(upper_bounds.get(key, 0), item["chunk_index"] + 1)
    for (space_id, doc_id), upper_bound in upper_bounds.items():
        await session.execute(
            delete(IntegrationKbChunkModel).where(
                IntegrationKbChunkModel.space_id == space_id,
                IntegrationKbChunkModel.doc_id == doc_id,
                IntegrationKbChunkModel.chunk_index >= upper_bound,
            )
        )
    return len(rows)


async def search_chunks(
    session: AsyncSession,
    query_embedding: Sequence[float],
    *,
    space_ids: Sequence[int] | None = None,
    doc_ids: Sequence[int] | None = None,
    top_k: int = 10,
    min_score: float | None = None,
) -> list[dict[str, Any]]:
    """按余弦相似度召回分片，返回按 score 降序的命中列表。

    ``space_ids`` 为问答范围（空表示全空间）；``doc_ids`` 供单文档内检索（如「就这篇文档提问」）。
    score = 1 - cosine_distance，取值 [0, 2]，越大越相似；min_score 用于过滤低相关命中。
    排序在数据库侧完成，应用侧只做分数换算与截断。
    """
    _require_postgres()
    if top_k <= 0:
        raise CustomException(msg="top_k 必须大于 0", status_code=status.HTTP_400_BAD_REQUEST)

    vector = validate_embedding(query_embedding)
    distance = IntegrationKbChunkModel.embedding.cosine_distance(vector).label("distance")
    stmt = select(
        IntegrationKbChunkModel.id,
        IntegrationKbChunkModel.space_id,
        IntegrationKbChunkModel.doc_id,
        IntegrationKbChunkModel.chunk_index,
        IntegrationKbChunkModel.page_no,
        IntegrationKbChunkModel.section_title,
        IntegrationKbChunkModel.content,
        IntegrationKbChunkModel.meta,
        distance,
    )
    if space_ids:
        stmt = stmt.where(IntegrationKbChunkModel.space_id.in_(list(space_ids)))
    if doc_ids:
        stmt = stmt.where(IntegrationKbChunkModel.doc_id.in_(list(doc_ids)))
    stmt = stmt.order_by(distance).limit(top_k)

    result = await session.execute(stmt)
    hits: list[dict[str, Any]] = []
    for row in result.mappings():
        score = 1.0 - float(row["distance"])
        if min_score is not None and score < min_score:
            continue
        hits.append(
            {
                "id": row["id"],
                "space_id": row["space_id"],
                "doc_id": row["doc_id"],
                "chunk_index": row["chunk_index"],
                "page_no": row["page_no"],
                "section_title": row["section_title"],
                "content": row["content"],
                "meta": row["meta"],
                "score": round(score, 6),
            }
        )
    return hits


async def delete_chunks(session: AsyncSession, *, doc_ids: Sequence[int]) -> int:
    """删除指定文档的全部分片，返回删除条数（文档删除/重新解析时调用）。"""
    _require_postgres()
    ids = [int(doc_id) for doc_id in doc_ids]
    if not ids:
        raise CustomException(msg="doc_ids 不能为空", status_code=status.HTTP_400_BAD_REQUEST)

    result = await session.execute(delete(IntegrationKbChunkModel).where(IntegrationKbChunkModel.doc_id.in_(ids)))
    return getattr(result, "rowcount", 0) or 0


async def count_chunks(session: AsyncSession, *, space_ids: Sequence[int] | None = None) -> int:
    """统计分片数量（可按知识空间过滤），用于运营统计与运维自检。"""
    _require_postgres()
    stmt = select(func.count()).select_from(IntegrationKbChunkModel)
    if space_ids:
        stmt = stmt.where(IntegrationKbChunkModel.space_id.in_(list(space_ids)))
    result = await session.execute(stmt)
    return int(result.scalar_one())
