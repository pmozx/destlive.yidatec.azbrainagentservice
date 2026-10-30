"""pgvector 向量存储（集成-知识库适配层）：绑定分片模型，能力来自 ``app.core.rag.vector_store``。

本模块只保留「集成库表结构」相关的参数（表名、索引名、冲突列、分组尾删列），
建表、幂等写入、余弦检索、删除与统计的实现全部复用 core，避免两套向量逻辑分叉。
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rag import vector_store as core_store

from .model import (
    INTEGRATION_KB_CHUNK_EMBEDDING_INDEX_NAME,
    INTEGRATION_KB_CHUNK_TABLE_NAME,
    IntegrationKbChunkModel,
)

# 冲突键与尾删分组：同一文档重跑不产生重复分片，条数变少时清掉旧尾块
_CONFLICT_COLUMNS = ("space_id", "doc_id", "chunk_index")
_UPDATE_COLUMNS = ("content", "page_no", "section_title", "token_count", "meta", "embedding")
_TAIL_GROUP_COLUMNS = ("space_id", "doc_id")

vector_store_ready = core_store.vector_store_ready
validate_embedding = core_store.validate_embedding


async def ensure_vector_store() -> None:
    """幂等创建 vector 扩展、分片表与 HNSW 索引（应用启动时调用）。"""
    await core_store.ensure_vector_store(
        IntegrationKbChunkModel,
        index_name=INTEGRATION_KB_CHUNK_EMBEDDING_INDEX_NAME,
        table_name=INTEGRATION_KB_CHUNK_TABLE_NAME,
    )


async def upsert_chunks(session: AsyncSession, chunks: Sequence[dict[str, Any]]) -> int:
    """按 (space_id, doc_id, chunk_index) 幂等写入分片，返回写入条数。"""
    now = datetime.now(UTC)
    rows = [
        {
            "space_id": item["space_id"],
            "doc_id": item["doc_id"],
            "chunk_index": int(item["chunk_index"]),
            "content": item["content"],
            "page_no": item.get("page_no"),
            "section_title": item.get("section_title"),
            "token_count": int(item.get("token_count") or len(item["content"])),
            "meta": item.get("meta") or {},
            "embedding": item["embedding"],
            "created_time": now,
        }
        for item in chunks
    ]
    return await core_store.upsert_chunks(
        session,
        IntegrationKbChunkModel,
        rows,
        conflict_columns=_CONFLICT_COLUMNS,
        update_columns=_UPDATE_COLUMNS,
        tail_group_columns=_TAIL_GROUP_COLUMNS,
        index_column="chunk_index",
    )


async def search_chunks(
    session: AsyncSession,
    query_embedding: Sequence[float],
    *,
    space_ids: Sequence[int] | None = None,
    doc_ids: Sequence[int] | None = None,
    top_k: int = 10,
    min_score: float | None = None,
) -> list[dict[str, Any]]:
    """按余弦相似度召回分片，返回按 score 降序的命中列表。"""
    return await core_store.search_chunks(
        session,
        IntegrationKbChunkModel,
        query_embedding,
        select_columns=(
            "id",
            "space_id",
            "doc_id",
            "chunk_index",
            "page_no",
            "section_title",
            "content",
            "meta",
        ),
        in_filters={"space_id": space_ids or (), "doc_id": doc_ids or ()},
        top_k=top_k,
        min_score=min_score,
    )


async def delete_chunks(session: AsyncSession, *, doc_ids: Sequence[int]) -> int:
    """删除指定文档的全部分片，返回删除条数（文档删除/重新解析时调用）。"""
    return await core_store.delete_chunks(session, IntegrationKbChunkModel, filters={"doc_id": doc_ids})


async def count_chunks(session: AsyncSession, *, space_ids: Sequence[int] | None = None) -> int:
    """统计分片数量（可按知识空间过滤），用于运营统计与运维自检。"""
    return await core_store.count_chunks(
        session,
        IntegrationKbChunkModel,
        in_filters={"space_id": space_ids or ()},
    )
