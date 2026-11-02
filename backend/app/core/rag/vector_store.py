"""pgvector 向量存储：建表/索引、幂等写入、余弦检索、分页浏览。

设计约定：
1. 仅 PostgreSQL + pgvector 可用；其它数据库调用即报错，不做静默降级，
   避免「看起来写入成功、实际检索不到」的隐性故障；
2. 表名遵循项目强命名规范 ``sbp_ai_[域]_[业务名]``：``sbp_ai_kb_vector_collection``（集合元数据）
   与 ``sbp_ai_kb_vector_embedding``（向量行）。一套表 + 按 collection 名逻辑隔离，
   业务字段全部落在 ``cmetadata``（JSONB，自带 GIN 索引），本模块不 import 任何业务模型；
3. **不使用 langchain-postgres 的 ``PGVector``**：该实现的表名在类定义里硬编码为
   ``langchain_pg_collection`` / ``langchain_pg_embedding``，构造参数无法覆盖，也没有扩展点，
   与项目「表名必须 ``sbp_ai_`` 开头」的强规范冲突。此处用 SQLAlchemy Core 直接写 SQL，
   表名、索引名、维度全部本模块自持；对外仍是 :func:`ensure_vector_store` /
   :func:`upsert_documents` / :func:`search_documents` 等函数，调用方无感；
4. **驱动与线程模型**：向量库单独用 psycopg v3 的**同步**连接池（与会走 asyncpg 的业务表完全隔离）；
   对外仍是 async 函数，内部 ``asyncio.to_thread`` 丢进线程池，事件循环不被阻塞，
   与项目里 ``run_in_parse_pool`` 处理阻塞调用的口径一致；
5. 主键由调用方给**确定性 id**（同一文档重跑得到同一 id）：写入是 ``INSERT ... ON CONFLICT (id) DO UPDATE``，
   天然幂等，不需要业务侧维护唯一约束，也不用「先删后插」；
6. embedding 由调用方生成后传入，维度校验在此收口（建表时也用 ``VECTOR_DIM`` 固定列宽）；
7. 距离度量固定 cosine，检索分数统一换算为 ``1 - cosine_distance``（越大越相似）；
8. 表与扩展的创建是幂等的：重复执行无副作用。

> **与 ``sbp_ai_kb_chunk`` 的关系**：kb 域的业务分片表 ``sbp_ai_kb_chunk`` 自带 ``embedding`` 列与
> HNSW 索引，是契约 #03 写入与检索的落点。本模块是**通用向量层**（不依赖任何业务模型），
> 目前**没有调用方**，仅 :func:`validate_embedding` 被 ``kb/publication/service.py`` 复用做维度校验。
> 后续检索能力接入时须二选一收敛，不要两套并存写同一份数据。
"""

import asyncio
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any, Final

from fastapi import status
from sqlalchemy import Engine, bindparam, create_engine, text
from sqlalchemy.orm import Session

from app.common.enums import DatabaseTypeEnum
from app.config.setting import settings
from app.core.exceptions import CustomException
from app.core.logger import logger

# 向量层表名（遵循 sbp_ai_[域]_[业务名]；窄 SQL 需要直接引用）
COLLECTION_TABLE: Final[str] = "sbp_ai_kb_vector_collection"
EMBEDDING_TABLE: Final[str] = "sbp_ai_kb_vector_embedding"

# 索引名（建索引是幂等的，名称固定便于运维直接按名重建）
COLLECTION_ID_INDEX: Final[str] = "ix_sbp_ai_kb_vector_embedding_collection_id"
METADATA_INDEX: Final[str] = "ix_sbp_ai_kb_vector_embedding_cmetadata"
VECTOR_INDEX: Final[str] = "ix_sbp_ai_kb_vector_embedding_hnsw"

# 允许在分页浏览里排序的元数据字段（白名单，防止标识符注入）
_SORTABLE_FIELDS: Final[frozenset[str]] = frozenset(
    {"doc_id", "chunk_index", "page_no", "token_count", "space_id"}
)

# 索引名只允许字母数字下划线（index_name 拼进 DDL，不接受任意标识符）
_IDENTIFIER_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")

# 向量库专用 psycopg 同步连接池（见模块 docstring 第 4 条）
_VECTOR_ENGINE: Engine | None = None

# 本进程内表结构是否已就绪：读写入口先过一遍懒建，避免调用方忘调 ensure_vector_store
_SCHEMA_READY: bool = False


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
    global _VECTOR_ENGINE, _SCHEMA_READY
    if _VECTOR_ENGINE is not None:
        await asyncio.to_thread(_VECTOR_ENGINE.dispose)
    _VECTOR_ENGINE = None
    _SCHEMA_READY = False


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


def _vector_literal(values: Sequence[float]) -> str:
    """把向量转成 pgvector 的字面量文本（``[0.1,0.2,...]``），SQL 里再 CAST 成 vector。

    不依赖驱动的自定义类型注册：参数是纯文本，跨连接池、跨会话都稳定。
    """
    return "[" + ",".join(f"{value:.8g}" for value in values) + "]"


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


def _get_or_create_collection_uuid(conn: Session, collection: str) -> str:
    """取 collection 主键，不存在则创建（并发下靠 name 唯一约束兜底）。"""
    existing = _collection_uuid(conn, collection)
    if existing:
        return existing
    conn.execute(
        text(f"INSERT INTO {COLLECTION_TABLE} (name) VALUES (:name) ON CONFLICT (name) DO NOTHING"),
        {"name": collection},
    )
    created = _collection_uuid(conn, collection)
    if created is None:
        raise CustomException(
            msg=f"向量集合 {collection} 创建失败（{COLLECTION_TABLE}.name 唯一冲突）",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    return created


def _create_schema(conn: Session, index_name: str | None = None) -> None:
    """建扩展、两张向量表与索引（全部 IF NOT EXISTS，幂等），并校验既有表维度。"""
    if index_name is not None and not _IDENTIFIER_RE.match(index_name):
        raise CustomException(
            msg=f"非法索引名 {index_name!r}：只允许字母、数字、下划线且不超过 63 字符",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    resolved_index = index_name or VECTOR_INDEX
    dimension = int(settings.VECTOR_DIM)

    conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    conn.execute(
        text(
            f"CREATE TABLE IF NOT EXISTS {COLLECTION_TABLE} ("
            "uuid uuid PRIMARY KEY DEFAULT gen_random_uuid(), "
            "name varchar(200) NOT NULL UNIQUE, "
            "cmetadata jsonb)"
        )
    )
    conn.execute(
        text(
            f"CREATE TABLE IF NOT EXISTS {EMBEDDING_TABLE} ("
            "id varchar(200) PRIMARY KEY, "
            f"collection_id uuid NOT NULL REFERENCES {COLLECTION_TABLE} (uuid) ON DELETE CASCADE, "
            f"embedding vector({dimension}) NOT NULL, "
            "document text, "
            "cmetadata jsonb)"
        )
    )
    conn.execute(text(f"CREATE INDEX IF NOT EXISTS {COLLECTION_ID_INDEX} ON {EMBEDDING_TABLE} (collection_id)"))
    conn.execute(
        text(
            f"CREATE INDEX IF NOT EXISTS {METADATA_INDEX} ON {EMBEDDING_TABLE} "
            "USING gin (cmetadata jsonb_path_ops)"
        )
    )

    existing_dim = _existing_vector_dim(conn, EMBEDDING_TABLE)
    if existing_dim is not None and existing_dim != dimension:
        raise CustomException(
            msg=(
                f"分片表 {EMBEDDING_TABLE} 的向量维度为 {existing_dim}，与 VECTOR_DIM={dimension} 不一致；"
                "换维度无法原地修改，需重建表并重新灌入向量"
            )
        )
    conn.execute(
        text(
            f"CREATE INDEX IF NOT EXISTS {resolved_index} ON {EMBEDDING_TABLE} "
            "USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
        )
    )


def _ensure_schema_once() -> None:
    """读写入口的懒建：本进程首次访问时补建表结构（VECTOR_AUTO_CREATE 关闭则跳过）。

    调用方可能直接调 upsert/search 而不先调 :func:`ensure_vector_store`，
    这里兜住「表不存在」的情况；DDL 全部幂等，多进程并发同时执行也无副作用。
    """
    global _SCHEMA_READY
    if _SCHEMA_READY or not settings.VECTOR_AUTO_CREATE:
        return
    with _vector_engine().begin() as conn:
        _create_schema(conn)
    _SCHEMA_READY = True


def _ensure_vector_store(collection: str, index_name: str | None) -> None:
    global _SCHEMA_READY
    if not (vector_store_ready() and settings.VECTOR_AUTO_CREATE):
        return

    with _vector_engine().begin() as conn:
        _create_schema(conn, index_name)
        # 集合元数据顺手建好，后续写入不必再判断集合是否存在
        conn.execute(
            text(f"INSERT INTO {COLLECTION_TABLE} (name) VALUES (:name) ON CONFLICT (name) DO NOTHING"),
            {"name": collection},
        )
    _SCHEMA_READY = True

    logger.info(
        "==== 向量存储就绪（表 {}，collection {}，维度 {}，索引 HNSW/cosine）====",
        EMBEDDING_TABLE,
        collection,
        settings.VECTOR_DIM,
    )


async def ensure_vector_store(collection: str, *, index_name: str | None = None) -> None:
    """幂等创建 vector 扩展、向量表、collection 与 HNSW 索引（应用启动时调用）。

    HNSW + vector_cosine_ops 与检索口径（``<=>`` 余弦距离）保持一致；
    ``index_name`` 不给时用固定名 ``ix_sbp_ai_kb_vector_embedding_hnsw``——
    HNSW 索引建在表上、与 collection 无关，不必按 collection 各建一份。
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


def _split_search_filters(
    filters: Mapping[str, Any] | None,
    in_filters: Mapping[str, Sequence[Any]] | None,
) -> tuple[dict[str, Any], dict[str, Sequence[Any]]]:
    """拆出检索过滤：``{"k": v}`` 走等值，``{"k": {"$in": [...]}}`` 走集合（兼容原 PGVector 语法）。"""
    equals: dict[str, Any] = {}
    in_sets: dict[str, Sequence[Any]] = dict(in_filters or {})
    for name, value in (filters or {}).items():
        if isinstance(value, Mapping) and "$in" in value:
            in_sets[name] = list(value["$in"] or [])
        else:
            equals[name] = value
    return equals, in_sets


def _delete_tail(collection: str, *, group: Mapping[str, Any], index_field: str, upper_bound: int) -> None:
    """删除同分组下序号 >= upper_bound 的旧尾块（重跑后条数变少时清理多余分片）。"""
    _ensure_schema_once()
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


_UPSERT_SQL: Final[str] = (
    f"INSERT INTO {EMBEDDING_TABLE} (id, collection_id, embedding, document, cmetadata) "
    "VALUES (:id, CAST(:collection_id AS uuid), CAST(:vec AS vector), :document, CAST(:metadata AS jsonb)) "
    "ON CONFLICT (id) DO UPDATE SET "
    "collection_id = EXCLUDED.collection_id, "
    "embedding = EXCLUDED.embedding, "
    "document = EXCLUDED.document, "
    "cmetadata = EXCLUDED.cmetadata"
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
      避免重跑后条数变少时旧分片永不更新。
    """
    if not documents:
        return 0

    _ensure_schema_once()
    rows = [
        {
            "id": str(item["id"]),
            "vec": _vector_literal(validate_embedding(item["embedding"])),
            "document": str(item["content"]),
            "metadata": json.dumps(dict(item.get("metadata") or {}), ensure_ascii=False),
        }
        for item in documents
    ]

    with _vector_engine().begin() as conn:
        collection_id = _get_or_create_collection_uuid(conn, collection)
        conn.execute(
            text(_UPSERT_SQL),
            [{**row, "collection_id": collection_id} for row in rows],
        )

    if tail_group and index_field:
        upper_bound = max(int(item.get("metadata", {}).get(index_field) or 0) for item in documents) + 1
        _delete_tail(collection, group=tail_group, index_field=index_field, upper_bound=upper_bound)
    return len(rows)


async def upsert_documents(
    collection: str,
    documents: Sequence[Mapping[str, Any]],
    *,
    tail_group: Mapping[str, Any] | None = None,
    index_field: str = "chunk_index",
) -> int:
    """按确定性 id 幂等写入分片（``ON CONFLICT (id) DO UPDATE``），返回写入条数。"""
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
    vector = _vector_literal(validate_embedding(query_embedding))
    equals, in_sets = _split_search_filters(filters, in_filters)
    _ensure_schema_once()

    with _vector_engine().begin() as conn:
        collection_id = _collection_uuid(conn, collection)
        if collection_id is None:
            return []
        where_sql, params = _where_sql(equals, in_sets, None)
        condition = f" AND {where_sql}" if where_sql else ""
        rows = conn.execute(
            text(
                f"SELECT id, document, cmetadata, embedding <=> CAST(:vec AS vector) AS distance "
                f"FROM {EMBEDDING_TABLE} "
                "WHERE collection_id = CAST(:collection_id AS uuid)"
                f"{condition} ORDER BY embedding <=> CAST(:vec AS vector) LIMIT :top_k"
            ),
            {**params, "collection_id": collection_id, "vec": vector, "top_k": top_k},
        ).all()

    results: list[dict[str, Any]] = []
    for row in rows:
        score = 1.0 - float(row[3])
        if min_score is not None and score < min_score:
            continue
        results.append(
            {
                **dict(row[2] or {}),
                "id": str(row[0]),
                "content": row[1] or "",
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

    - ``filters``：等值过滤（如 ``{"doc_id": 12}``），也兼容 ``{"doc_id": {"$in": [1, 2]}}``；
    - ``in_filters``：集合过滤（如 ``{"space_id": [...]}``，空集合视为「不限」）；
    - score = 1 - cosine_distance，取值 [0, 2]，越大越相似；``min_score`` 过滤低相关命中。
    """
    _require_postgres()
    if top_k <= 0:
        raise CustomException(msg="top_k 必须大于 0", status_code=status.HTTP_400_BAD_REQUEST)
    return await asyncio.to_thread(
        _search_documents, collection, query_embedding, filters, in_filters, top_k, min_score
    )


def _delete_documents(
    collection: str,
    ids: Sequence[str] | None,
    filters: Mapping[str, Sequence[Any]] | None,
) -> int:
    _ensure_schema_once()
    if ids:
        id_list = [str(item) for item in ids]
        with _vector_engine().begin() as conn:
            collection_id = _collection_uuid(conn, collection)
            if collection_id is None:
                return 0
            conn.execute(
                text(
                    f"DELETE FROM {EMBEDDING_TABLE} "
                    "WHERE collection_id = CAST(:collection_id AS uuid) AND id IN :ids"
                ).bindparams(bindparam("ids", expanding=True)),
                {"collection_id": collection_id, "ids": id_list},
            )
        return len(set(id_list))

    clauses_needed = {key: values for key, values in (filters or {}).items() if values}
    if not clauses_needed:
        raise CustomException(msg="删除条件不能为空", status_code=status.HTTP_400_BAD_REQUEST)

    with _vector_engine().begin() as conn:
        collection_id = _collection_uuid(conn, collection)
        if collection_id is None:
            return 0
        where_sql, params = _where_sql(None, clauses_needed, None)
        result = conn.execute(
            text(
                f"DELETE FROM {EMBEDDING_TABLE} "
                f"WHERE collection_id = CAST(:collection_id AS uuid) AND {where_sql}"
            ),
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
    _ensure_schema_once()
    with _vector_engine().begin() as conn:
        collection_id = _collection_uuid(conn, collection)
        if collection_id is None:
            return 0
        where_sql, params = _where_sql(filters, in_filters, None)
        condition = f" AND {where_sql}" if where_sql else ""
        result = conn.execute(
            text(
                f"SELECT count(*) FROM {EMBEDDING_TABLE} "
                f"WHERE collection_id = CAST(:collection_id AS uuid){condition}"
            ),
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
    _ensure_schema_once()
    with _vector_engine().begin() as conn:
        collection_id = _collection_uuid(conn, collection)
        if collection_id is None:
            return [], 0

        where_sql, params = _where_sql(filters, None, like_filters)
        condition = f" AND {where_sql}" if where_sql else ""
        total = int(
            conn.execute(
                text(
                    f"SELECT count(*) FROM {EMBEDDING_TABLE} "
                    f"WHERE collection_id = CAST(:collection_id AS uuid){condition}"
                ),
                {**params, "collection_id": collection_id},
            ).scalar_one()
        )
        if total == 0:
            return [], 0

        order_parts: list[str] = []
        for field, direction in order_by:
            if field not in _SORTABLE_FIELDS:
                continue
            order_parts.append(
                f"(cmetadata->>'{field}')::int {'DESC' if str(direction).lower() == 'desc' else 'ASC'}"
            )
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
