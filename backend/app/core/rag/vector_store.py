"""pgvector 向量存储（通用能力）：建表/索引、幂等写入、余弦相似度检索。

设计约定：
1. 仅 PostgreSQL + pgvector 可用；其它数据库调用即报错，不做静默降级，
   避免「看起来写入成功、实际检索不到」的隐性故障；
2. 表模型由调用方注入（知识平台传 ``KbChunkModel``），本模块不 import 任何业务模型，
   保持 core → modules 的单向依赖；
3. 一个库只保留一套向量维度（由 VECTOR_DIM 决定），换维度必须重建表，
   ensure 阶段会比对已有表的实际维度并提示；
4. embedding 由调用方生成后传入，本模块不绑定具体模型供应商；维度校验在此收口；
5. 距离度量固定 cosine，检索分数统一换算为 ``1 - cosine_distance``（越大越相似）；
6. 表与扩展的创建是幂等的：initdb 脚本已建过也没关系，重复执行无副作用。
"""

from collections.abc import Mapping, Sequence
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


async def _existing_vector_dim(conn, table_name: str) -> int | None:
    """读取已有表中 embedding 列的实际维度（表不存在时返回 None）。"""
    result = await conn.execute(
        text(
            "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
            "WHERE attrelid = to_regclass(:table_name) AND attname = 'embedding' AND attnum > 0"
        ),
        {"table_name": table_name},
    )
    row = result.first()
    if row is None or not row[0]:
        return None
    # format_type 返回形如 vector(1024)；未声明维度时为 vector
    spec = str(row[0])
    if "(" not in spec:
        return None
    return int(spec.split("(")[1].rstrip(")"))


async def ensure_vector_store(
    model: type,
    *,
    index_name: str | None = None,
    table_name: str | None = None,
) -> None:
    """幂等创建 vector 扩展、分片表与 HNSW 索引（应用启动时调用）。

    HNSW + vector_cosine_ops 与检索口径（<=> 余弦距离）保持一致；索引参数 m/ef_construction
    取 pgvector 默认值，数据量增长后可按召回率单独调整。
    ``index_name`` 不给时按 ``ix_{表名}_embedding_hnsw`` 推导。
    """
    if not (vector_store_ready() and settings.VECTOR_AUTO_CREATE):
        return

    resolved_table = table_name or model.__tablename__
    resolved_index = index_name or f"ix_{resolved_table}_embedding_hnsw"

    async with async_engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(model.__table__.create, checkfirst=True)
        existing_dim = await _existing_vector_dim(conn, resolved_table)
        if existing_dim is not None and existing_dim != settings.VECTOR_DIM:
            raise CustomException(
                msg=(
                    f"分片表 {resolved_table} 的向量维度为 {existing_dim}，与 VECTOR_DIM={settings.VECTOR_DIM} 不一致；"
                    "换维度无法原地修改，需重建表并重新灌入向量"
                )
            )
        await conn.execute(
            text(
                f"CREATE INDEX IF NOT EXISTS {resolved_index} ON {resolved_table} "
                "USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
            )
        )

    logger.info(
        "==== 向量存储就绪（表 {}，维度 {}，索引 HNSW/cosine）====",
        resolved_table,
        settings.VECTOR_DIM,
    )


async def upsert_chunks(
    session: AsyncSession,
    model: type,
    chunks: Sequence[dict[str, Any]],
    *,
    conflict_columns: Sequence[str],
    update_columns: Sequence[str] | None = None,
    tail_group_columns: Sequence[str] = (),
    index_column: str | None = None,
) -> int:
    """按 ``conflict_columns`` 幂等写入分片，返回写入条数。

    - ``conflict_columns``：唯一键（如知识平台 ``record_id`` + ``seq``，集成库 ``space_id`` + ``doc_id`` + ``chunk_index``）；
    - ``update_columns``：冲突时覆盖的列，默认取首行除冲突列外的全部列；
    - ``tail_group_columns`` + ``index_column``：给出后按分组删除超出本次分片上界的旧尾块，
      避免重跑后条数变少时旧 ``seq`` 永不更新（与原始集成库行为保持一致）。
    每个元素须包含 ``embedding``，其余列由调用方按自身模型给出。
    """
    _require_postgres()
    if not chunks:
        return 0

    rows: list[dict[str, Any]] = []
    for item in chunks:
        row = {key: value for key, value in item.items()}
        row["embedding"] = validate_embedding(item["embedding"])
        rows.append(row)

    conflict_names = list(conflict_columns)
    update_names = list(update_columns) if update_columns is not None else [key for key in rows[0] if key not in conflict_names]
    stmt = insert(model).values(rows)
    set_values = {name: stmt.excluded[name] for name in update_names}
    # 有 updated_time 列时用数据库时间，避免多实例应用时钟漂移导致更新时间回退
    if hasattr(model, "updated_time") and "updated_time" not in set_values:
        set_values["updated_time"] = func.now()
    stmt = stmt.on_conflict_do_update(
        index_elements=[getattr(model, name) for name in conflict_names],
        set_=set_values,
    )
    await session.execute(stmt)

    if tail_group_columns and index_column:
        upper_bounds: dict[tuple[Any, ...], int] = {}
        for item in rows:
            key = tuple(item[name] for name in tail_group_columns)
            upper_bounds[key] = max(upper_bounds.get(key, 0), int(item[index_column]) + 1)
        for key, upper_bound in upper_bounds.items():
            conditions = [getattr(model, name) == value for name, value in zip(tail_group_columns, key, strict=True)]
            await session.execute(
                delete(model).where(*conditions, getattr(model, index_column) >= upper_bound)
            )
    return len(rows)


async def search_chunks(
    session: AsyncSession,
    model: type,
    query_embedding: Sequence[float],
    *,
    select_columns: Sequence[str],
    filters: Mapping[str, Any] | None = None,
    in_filters: Mapping[str, Sequence[Any]] | None = None,
    top_k: int = 10,
    min_score: float | None = None,
) -> list[dict[str, Any]]:
    """按余弦相似度召回分片，返回按 score 降序的命中列表。

    - ``select_columns``：需要回传的模型列名；
    - ``filters``：等值过滤（如 ``{"status": "online"}``）；
    - ``in_filters``：集合过滤（如 ``{"record_id": [...]}``，空集合视为「不限」）；
    - score = 1 - cosine_distance，取值 [0, 2]，越大越相似；``min_score`` 过滤低相关命中。
    排序在数据库侧完成，应用侧只做分数换算与截断。
    """
    _require_postgres()
    if top_k <= 0:
        raise CustomException(msg="top_k 必须大于 0", status_code=status.HTTP_400_BAD_REQUEST)

    vector = validate_embedding(query_embedding)
    distance = model.embedding.cosine_distance(vector).label("distance")
    stmt = select(*(getattr(model, name) for name in select_columns), distance)
    for name, value in (filters or {}).items():
        stmt = stmt.where(getattr(model, name) == value)
    for name, values in (in_filters or {}).items():
        if values:
            stmt = stmt.where(getattr(model, name).in_(list(values)))
    stmt = stmt.order_by(distance).limit(top_k)

    result = await session.execute(stmt)
    hits: list[dict[str, Any]] = []
    for row in result.mappings():
        score = 1.0 - float(row["distance"])
        if min_score is not None and score < min_score:
            continue
        hit = {name: row[name] for name in select_columns}
        hit["score"] = round(score, 6)
        hits.append(hit)
    return hits


async def delete_chunks(
    session: AsyncSession,
    model: type,
    *,
    filters: Mapping[str, Sequence[Any]],
) -> int:
    """按过滤条件删除分片，返回删除条数（文档删除/重新解析时调用）。"""
    _require_postgres()
    conditions = []
    for name, values in filters.items():
        values = list(values)
        if not values:
            raise CustomException(msg=f"{name} 不能为空", status_code=status.HTTP_400_BAD_REQUEST)
        conditions.append(getattr(model, name).in_(values))
    if not conditions:
        raise CustomException(msg="删除条件不能为空", status_code=status.HTTP_400_BAD_REQUEST)

    result = await session.execute(delete(model).where(*conditions))
    return getattr(result, "rowcount", 0) or 0


async def count_chunks(
    session: AsyncSession,
    model: type,
    *,
    filters: Mapping[str, Any] | None = None,
    in_filters: Mapping[str, Sequence[Any]] | None = None,
) -> int:
    """统计分片数量（可按等值/集合过滤收敛），用于运营统计与运维自检。"""
    _require_postgres()
    stmt = select(func.count()).select_from(model)
    for name, value in (filters or {}).items():
        stmt = stmt.where(getattr(model, name) == value)
    for name, values in (in_filters or {}).items():
        if values:
            stmt = stmt.where(getattr(model, name).in_(list(values)))
    result = await session.execute(stmt)
    return int(result.scalar_one())
