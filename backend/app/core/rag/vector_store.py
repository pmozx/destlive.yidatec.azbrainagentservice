"""pgvector 向量存储（langchain-postgres 的 ``PGVector``）：建表/索引、幂等写入、余弦检索。

设计约定：
1. 仅 PostgreSQL + pgvector 可用；其它数据库调用即报错，不做静默降级，
   避免「看起来写入成功、实际检索不到」的隐性故障；
2. 一套通用表（``langchain_pg_collection`` / ``langchain_pg_embedding``）+ 按 collection 名隔离，
   业务字段全部落在 ``cmetadata``（JSONB，自带 GIN 索引），本模块不 import 任何业务模型；
3. **驱动与线程模型**：PGVector 只支持 psycopg v3，且 psycopg 的异步模式不接受 Windows 默认的
   Proactor 事件循环；建扩展语句又是「多条 SQL 拼一次执行」（asyncpg 直接报
   "cannot insert multiple commands into a prepared statement"）。因此：
   - 向量库单独用 psycopg 的**同步**连接池（与会走 asyncpg 的业务表完全隔离）；
   - 对外仍是 async 函数，内部 ``asyncio.to_thread`` 丢进线程池，事件循环不被阻塞，
     与项目里 ``run_in_parse_pool`` 处理阻塞调用的口径一致；
4. 主键由调用方给**确定性 id**（同一文档重跑得到同一 id）：PGVector 的
   ``add_embeddings`` 是 ``INSERT ... ON CONFLICT (id) DO UPDATE``，天然幂等，
   不需要业务侧维护唯一约束，也不用「先删后插」；
5. embedding 由调用方生成后传入，维度校验在此收口（建表时也用 ``embedding_length`` 固定列宽）；
6. 距离度量固定 cosine，检索分数统一换算为 ``1 - cosine_distance``（越大越相似）；
7. PGVector 未提供「按 metadata 区间删除」与分页浏览，这几条窄 SQL 收口在本模块，
   业务侧只调函数、不写 SQL；
8. 表与扩展的创建是幂等的：重复执行无副作用。
"""

import asyncio
import json
from collections.abc import Mapping, Sequence
from typing import Any, Final

from fastapi import status
from langchain_core.embeddings import Embeddings
from langchain_postgres import PGVector
from langchain_postgres.vectorstores import DistanceStrategy
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session

from app.common.enums import DatabaseTypeEnum
from app.config.setting import settings
from app.core.exceptions import CustomException
from app.core.logger import logger

# PGVector 固定表名（建索引与窄 SQL 需要直接引用）
COLLECTION_TABLE: Final[str] = "langchain_pg_collection"
EMBEDDING_TABLE: Final[str] = "langchain_pg_embedding"

# 允许在分页浏览里排序的元数据字段（白名单，防止标识符注入）
_SORTABLE_FIELDS: Final[frozenset[str]] = frozenset(
    {"doc_id", "chunk_index", "page_no", "token_count", "space_id"}
)


class _PrecomputedEmbeddings(Embeddings):
    """占位 embedding：向量由业务侧算好后直接传入，PGVector 不会调用这两个方法。"""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError("向量由调用方预先计算，请使用 add_embeddings 直接写入")

    def embed_query(self, text: str) -> list[float]:
        raise NotImplementedError("查询向量由调用方预先计算，请使用 similarity_search_by_vector")


_PLACEHOLDER = _PrecomputedEmbeddings()

# 每个 collection 一个 PGVector 实例：实例构造时会建表再缓存，避免每次调用都建表
_STORES: dict[str, PGVector] = {}

# 向量库专用 psycopg 同步连接池（见模块 docstring 第 3 条）
_VECTOR_ENGINE: Engine | None = None


def _vector_engine() -> Engine:
    """惰性创建 psycopg v3 同步连接池（会话在线程池里跑，不占用事件循环）。"""
    global _VECTOR_ENGINE
    if _VECTOR_ENGINE is None:
        url = settings.psycopg_url
        if not url:
            raise CustomException(
                msg="向量存储需要 PostgreSQL 连接串（当前 DATABASE_TYPE 不支持）",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        _VECTOR_ENGINE = create_engine(
            url.replace("postgresql://", "postgresql+psycopg://", 1),
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
        )
    return _VECTOR_ENGINE


async def close_vector_store() -> None:
    """应用关停时释放向量库连接池。"""
    global _VECTOR_ENGINE
    if _VECTOR_ENGINE is not None:
        await asyncio.to_thread(_VECTOR_ENGINE.dispose)
    _VECTOR_ENGINE = None
    _STORES.clear()


def vector_store_ready() -> bool:
    """当前配置是否具备向量能力（postgres 且开关打开）。"""
    return settings.VECTOR_ENABLE and settings.DATABASE_TYPE == DatabaseTypeEnum.POSTGRES.value


def _require_postgres() -> None:
    """向量能力不可用时抛出可读异常（而不是让驱动报扩展不存在）。"""
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


def get_store(collection: str) -> PGVector:
    """取（或惰性构造）指定 collection 的 PGVector 实例，复用向量库专用连接池。"""
    store = _STORES.get(collection)
    if store is None:
        _require_postgres()
        store = PGVector(
            embeddings=_PLACEHOLDER,
            connection=_vector_engine(),
            embedding_length=settings.VECTOR_DIM,
            collection_name=collection,
            distance_strategy=DistanceStrategy.COSINE,
            # 只允许异步入口（本模块统一 to_thread 调用），实例构造时即完成建表/建 collection
            async_mode=False,
        )
        _STORES[collection] = store
    return store


def _existing_vector_dim(conn: Session, table_name: str) -> int | None:
    """读取已有表中 embedding 列的实际维度（表不存在时返回 None）。"""
    row = conn.execute(
        text(
            "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
            "WHERE attrelid = to_regclass(:table_name) AND attname = 'embedding' AND attnum > 0"
        ),
        {"table_name": table_name},
    ).first()
    if row is None or not row[0]:
        return None
    # format_type 返回形如 vector(1024)；未声明维度时为 vector
    spec = str(row[0])
    if "(" not in spec:
        return None
    return int(spec.split("(")[1].rstrip(")"))


def _collection_uuid(conn: Session, collection: str) -> str | None:
    """取 collection 的主键（窄 SQL 都要按它收敛范围，避免跨 collection 误删）。"""
    row = conn.execute(
        text(f"SELECT uuid FROM {COLLECTION_TABLE} WHERE name = :name"),
        {"name": collection},
    ).first()
    return str(row[0]) if row else None


def _ensure_vector_store(collection: str, index_name: str | None) -> None:
    if not (vector_store_ready() and settings.VECTOR_AUTO_CREATE):
        return

    store = get_store(collection)
    store.create_vector_extension()
    store.create_tables_if_not_exists()
    store.create_collection()

    resolved_index = index_name or f"ix_{EMBEDDING_TABLE}_{collection}_hnsw"
    with _vector_engine().begin() as conn:
        existing_dim = _existing_vector_dim(conn, EMBEDDING_TABLE)
        if existing_dim is not None and existing_dim != settings.VECTOR_DIM:
            raise CustomException(
                msg=(
                    f"分片表 {EMBEDDING_TABLE} 的向量维度为 {existing_dim}，与 VECTOR_DIM={settings.VECTOR_DIM} 不一致；"
                    "换维度无法原地修改，需重建表并重新灌入向量"
                )
            )
        conn.execute(
            text(
                f"CREATE INDEX IF NOT EXISTS {resolved_index} ON {EMBEDDING_TABLE} "
                "USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
            )
        )

    logger.info(
        "==== 向量存储就绪（collection {}，维度 {}，索引 HNSW/cosine）====",
        collection,
        settings.VECTOR_DIM,
    )


async def ensure_vector_store(collection: str, *, index_name: str | None = None) -> None:
    """幂等创建 vector 扩展、PGVector 表、collection 与 HNSW 索引（应用启动时调用）。

    HNSW + vector_cosine_ops 与检索口径（``<=>`` 余弦距离）保持一致；PGVector 自身只建
    cmetadata 的 GIN 索引，向量索引由这里补上。``index_name`` 不给时按
    ``ix_langchain_pg_embedding_{collection}_hnsw`` 推导。
    """
    await asyncio.to_thread(_ensure_vector_store, collection, index_name)


def _where_sql(
    filters: Mapping[str, Any] | None,
    in_filters: Mapping[str, Sequence[Any]] | None,
    like_filters: Mapping[str, str] | None,
) -> tuple[str, dict[str, Any]]:
    """把等值/集合/模糊过滤拼成 SQL 片段与参数（键名只来自调用方常量，不接受外部输入）。"""
    clauses: list[str] = []
    params: dict[str, Any] = {}
    for name, value in (filters or {}).items():
        if value is None:
            continue
        clauses.append(f"cmetadata->>'{name}' = :eq_{name}")
        params[f"eq_{name}"] = str(value)
    for name, values in (in_filters or {}).items():
        if not values:
            continue
        clauses.append(f"cmetadata->>'{name}' = ANY(:in_{name})")
        params[f"in_{name}"] = [str(item) for item in values]
    for name, value in (like_filters or {}).items():
        if not value:
            continue
        clauses.append(f"document ILIKE :like_{name}")
        params[f"like_{name}"] = f"%{value}%"
    return (" AND ".join(clauses), params)


def _delete_tail(collection: str, *, group: Mapping[str, Any], index_field: str, upper_bound: int) -> None:
    """删除同分组下序号 >= upper_bound 的旧尾块（PGVector 没有区间过滤删除，只能走窄 SQL）。"""
    with _vector_engine().begin() as conn:
        collection_id = _collection_uuid(conn, collection)
        if collection_id is None:
            return
        conn.execute(
            text(
                f"DELETE FROM {EMBEDDING_TABLE} "
                "WHERE collection_id = CAST(:collection_id AS uuid) "
                "AND cmetadata @> CAST(:group AS jsonb) "
                f"AND (cmetadata->>'{index_field}')::int >= :upper_bound"
            ),
            {
                "collection_id": collection_id,
                # 直接序列化原值（不要转字符串）：cmetadata 里 space_id/doc_id 是数字，
                # JSONB 包含运算符 @> 区分 1 与 "1"，转成字符串会导致尾块删不掉
                "group": json.dumps(dict(group)),
                "upper_bound": upper_bound,
            },
        )


def _upsert_documents(
    collection: str,
    documents: Sequence[Mapping[str, Any]],
    tail_group: Mapping[str, Any] | None,
    index_field: str,
) -> int:
    """按确定性 id 幂等写入分片，返回写入条数。

    - ``documents`` 每项为 ``{"id","content","metadata","embedding"}``，metadata 即业务字段；
    - ``tail_group`` + ``index_field``：给出后按分组删除超出本次分片上界的旧尾块，
      避免重跑后条数变少时旧分片永不更新（与原手写向量层的行为保持一致）。
    """
    if not documents:
        return 0

    texts = [str(item["content"]) for item in documents]
    embeddings = [validate_embedding(item["embedding"]) for item in documents]
    metadatas = [dict(item.get("metadata") or {}) for item in documents]
    ids = [str(item["id"]) for item in documents]
    get_store(collection).add_embeddings(texts, embeddings, metadatas, ids)

    if tail_group and index_field:
        upper_bound = max(int(item.get("metadata", {}).get(index_field) or 0) for item in documents) + 1
        _delete_tail(collection, group=tail_group, index_field=index_field, upper_bound=upper_bound)
    return len(ids)


async def upsert_documents(
    collection: str,
    documents: Sequence[Mapping[str, Any]],
    *,
    tail_group: Mapping[str, Any] | None = None,
    index_field: str = "chunk_index",
) -> int:
    """按确定性 id 幂等写入分片（PGVector ``ON CONFLICT (id) DO UPDATE``），返回写入条数。"""
    _require_postgres()
    return await asyncio.to_thread(_upsert_documents, collection, documents, tail_group, index_field)


def _search_documents(
    collection: str,
    query_embedding: Sequence[float],
    filters: Mapping[str, Any] | None,
    in_filters: Mapping[str, Sequence[Any]] | None,
    top_k: int,
    min_score: float | None,
) -> list[dict[str, Any]]:
    vector = validate_embedding(query_embedding)
    filter_payload: dict[str, Any] = dict(filters or {})
    for name, values in (in_filters or {}).items():
        if values:
            filter_payload[name] = {"$in": list(values)}

    hits = get_store(collection).similarity_search_with_score_by_vector(
        vector, k=top_k, filter=filter_payload or None
    )
    results: list[dict[str, Any]] = []
    for document, distance in hits:
        score = 1.0 - float(distance)
        if min_score is not None and score < min_score:
            continue
        results.append(
            {
                **dict(document.metadata or {}),
                "id": document.id,
                "content": document.page_content,
                "score": round(score, 6),
            }
        )
    return results


async def search_documents(
    collection: str,
    query_embedding: Sequence[float],
    *,
    filters: Mapping[str, Any] | None = None,
    in_filters: Mapping[str, Sequence[Any]] | None = None,
    top_k: int = 10,
    min_score: float | None = None,
) -> list[dict[str, Any]]:
    """按余弦相似度召回分片，返回按 score 降序的命中列表。

    - ``filters``：等值过滤（如 ``{"doc_id": 12}``）；
    - ``in_filters``：集合过滤（如 ``{"space_id": [...]}``，空集合视为「不限」）；
    - score = 1 - cosine_distance，取值 [0, 2]，越大越相似；``min_score`` 过滤低相关命中。
    """
    _require_postgres()
    if top_k <= 0:
        raise CustomException(msg="top_k 必须大于 0", status_code=status.HTTP_400_BAD_REQUEST)
    return await asyncio.to_thread(_search_documents, collection, query_embedding, filters, in_filters, top_k, min_score)


def _delete_documents(
    collection: str,
    ids: Sequence[str] | None,
    filters: Mapping[str, Sequence[Any]] | None,
) -> int:
    if ids:
        get_store(collection).delete(ids=[str(item) for item in ids], collection_only=True)
        return len(set(ids))

    clauses_needed = {key: values for key, values in (filters or {}).items() if values}
    if not clauses_needed:
        raise CustomException(msg="删除条件不能为空", status_code=status.HTTP_400_BAD_REQUEST)

    with _vector_engine().begin() as conn:
        collection_id = _collection_uuid(conn, collection)
        if collection_id is None:
            return 0
        where_sql, params = _where_sql(None, clauses_needed, None)
        result = conn.execute(
            text(f"DELETE FROM {EMBEDDING_TABLE} WHERE collection_id = CAST(:collection_id AS uuid) AND {where_sql}"),
            {**params, "collection_id": collection_id},
        )
        return int(result.rowcount or 0)


async def delete_documents(
    collection: str,
    *,
    ids: Sequence[str] | None = None,
    filters: Mapping[str, Sequence[Any]] | None = None,
) -> int:
    """按 id 或 metadata 集合过滤删除分片，返回删除条数（文档删除/重新解析时调用）。"""
    _require_postgres()
    return await asyncio.to_thread(_delete_documents, collection, ids, filters)


def _count_documents(
    collection: str,
    filters: Mapping[str, Any] | None,
    in_filters: Mapping[str, Sequence[Any]] | None,
) -> int:
    with _vector_engine().begin() as conn:
        collection_id = _collection_uuid(conn, collection)
        if collection_id is None:
            return 0
        where_sql, params = _where_sql(filters, in_filters, None)
        condition = f" AND {where_sql}" if where_sql else ""
        result = conn.execute(
            text(f"SELECT count(*) FROM {EMBEDDING_TABLE} WHERE collection_id = CAST(:collection_id AS uuid){condition}"),
            {**params, "collection_id": collection_id},
        )
        return int(result.scalar_one())


async def count_documents(
    collection: str,
    *,
    filters: Mapping[str, Any] | None = None,
    in_filters: Mapping[str, Sequence[Any]] | None = None,
) -> int:
    """统计分片数量（可按等值/集合过滤收敛），用于运营统计与运维自检。"""
    _require_postgres()
    return await asyncio.to_thread(_count_documents, collection, filters, in_filters)


def _page_documents(
    collection: str,
    filters: Mapping[str, Any] | None,
    like_filters: Mapping[str, str] | None,
    offset: int,
    limit: int,
    order_by: Sequence[tuple[str, str]],
) -> tuple[list[dict[str, Any]], int]:
    with _vector_engine().begin() as conn:
        collection_id = _collection_uuid(conn, collection)
        if collection_id is None:
            return [], 0

        where_sql, params = _where_sql(filters, None, like_filters)
        condition = f" AND {where_sql}" if where_sql else ""
        total = int(
            conn.execute(
                text(f"SELECT count(*) FROM {EMBEDDING_TABLE} WHERE collection_id = CAST(:collection_id AS uuid){condition}"),
                {**params, "collection_id": collection_id},
            ).scalar_one()
        )
        if total == 0:
            return [], 0

        order_parts: list[str] = []
        for field, direction in order_by:
            if field not in _SORTABLE_FIELDS:
                continue
            order_parts.append(f"(cmetadata->>'{field}')::int {'DESC' if str(direction).lower() == 'desc' else 'ASC'}")
        order_sql = f" ORDER BY {', '.join(order_parts)}" if order_parts else ""

        rows = conn.execute(
            text(
                f"SELECT id, document, cmetadata FROM {EMBEDDING_TABLE} "
                f"WHERE collection_id = CAST(:collection_id AS uuid){condition}{order_sql} "
                "LIMIT :limit OFFSET :offset"
            ),
            {**params, "collection_id": collection_id, "limit": limit, "offset": offset},
        ).all()
        results = [
            {"id": str(row[0]), "content": row[1] or "", "metadata": dict(row[2] or {})}
            for row in rows
        ]
        return results, total


async def page_documents(
    collection: str,
    *,
    filters: Mapping[str, Any] | None = None,
    like_filters: Mapping[str, str] | None = None,
    offset: int = 0,
    limit: int = 10,
    order_by: Sequence[tuple[str, str]] = (("doc_id", "asc"), ("chunk_index", "asc")),
) -> tuple[list[dict[str, Any]], int]:
    """分页浏览分片（运营页面用），返回 ``(命中列表, 总数)``。

    排序字段走白名单（``_SORTABLE_FIELDS``），方向只允许 asc/desc，
    其余字段忽略——分页浏览不需要任意列排序，也不接受外部拼 SQL。
    """
    _require_postgres()
    return await asyncio.to_thread(_page_documents, collection, filters, like_filters, offset, limit, order_by)
