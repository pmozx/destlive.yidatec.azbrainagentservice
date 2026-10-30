"""集成-知识库分片的向量存储适配层：只保留业务口径，存储能力全部来自 ``app.core.rag.vector_store``。

口径：
1. 一个 collection（``sbp_ai_integration_kb_chunk``）承载集成库全部分片，业务字段落 cmetadata；
2. 分片主键用 ``uuid5(space_id, doc_id, chunk_index)`` 确定性生成：同一文档重跑得到同一 id，
   PGVector 的写入是 upsert，因此「重跑不重复、条数变少自动清尾块」都不需要额外约束；
3. 对外返回的字典键名保持与原 SQLAlchemy 版本一致（含 ``id`` / ``content`` / ``meta`` / ``score``），
   调用方（检索、统计、运营浏览）无需感知存储实现。
"""

from collections.abc import Mapping, Sequence
from typing import Any, Final
from uuid import NAMESPACE_URL, uuid5

from app.core.rag import vector_store as core_store

COLLECTION_NAME: Final[str] = "sbp_ai_integration_kb_chunk"
_INDEX_NAME: Final[str] = "ix_langchain_pg_embedding_integration_kb_chunk_hnsw"

vector_store_ready = core_store.vector_store_ready
validate_embedding = core_store.validate_embedding


def chunk_id(space_id: int, doc_id: int, chunk_index: int) -> str:
    """分片确定性主键：同一 (空间, 文档, 序号) 永远映射到同一 id。"""
    return str(uuid5(NAMESPACE_URL, f"{COLLECTION_NAME}:{space_id}:{doc_id}:{chunk_index}"))


def _to_metadata(item: Mapping[str, Any]) -> dict[str, Any]:
    """业务字段 → cmetadata（数值保持数值，便于按区间/等值过滤）。"""
    return {
        "space_id": int(item["space_id"]),
        "doc_id": int(item["doc_id"]),
        "chunk_index": int(item["chunk_index"]),
        "page_no": item.get("page_no"),
        "section_title": item.get("section_title"),
        "token_count": int(item.get("token_count") or len(str(item.get("content") or ""))),
        "meta": dict(item.get("meta") or {}),
    }


async def ensure_vector_store() -> None:
    """幂等创建 vector 扩展、PGVector 表、collection 与 HNSW 索引（应用启动时调用）。"""
    await core_store.ensure_vector_store(COLLECTION_NAME, index_name=_INDEX_NAME)


async def upsert_chunks(chunks: Sequence[Mapping[str, Any]]) -> int:
    """按 (space_id, doc_id, chunk_index) 幂等写入分片，返回写入条数。

    批次可能混有多篇文档（批量重解析），尾块清理按 (space_id, doc_id) 分组进行，
    否则会用 A 文档的上界删掉 B 文档的分片。
    """
    groups: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for item in chunks:
        space_id, doc_id = int(item["space_id"]), int(item["doc_id"])
        groups.setdefault((space_id, doc_id), []).append(
            {
                "id": chunk_id(space_id, doc_id, int(item["chunk_index"])),
                "content": str(item["content"]),
                "metadata": _to_metadata(item),
                "embedding": item["embedding"],
            }
        )

    written = 0
    for (space_id, doc_id), documents in groups.items():
        written += await core_store.upsert_documents(
            COLLECTION_NAME,
            documents,
            tail_group={"space_id": space_id, "doc_id": doc_id},
            index_field="chunk_index",
        )
    return written


async def search_chunks(
    query_embedding: Sequence[float],
    *,
    space_ids: Sequence[int] | None = None,
    doc_ids: Sequence[int] | None = None,
    top_k: int = 10,
    min_score: float | None = None,
) -> list[dict[str, Any]]:
    """按余弦相似度召回分片，返回按 score 降序的命中列表。"""
    return await core_store.search_documents(
        COLLECTION_NAME,
        query_embedding,
        in_filters={"space_id": space_ids or (), "doc_id": doc_ids or ()},
        top_k=top_k,
        min_score=min_score,
    )


async def delete_chunks(*, doc_ids: Sequence[int]) -> int:
    """删除指定文档的全部分片，返回删除条数（文档删除/重新解析时调用）。"""
    return await core_store.delete_documents(COLLECTION_NAME, filters={"doc_id": doc_ids})


async def count_chunks(*, space_ids: Sequence[int] | None = None) -> int:
    """统计分片数量（可按知识空间过滤），用于运营统计与运维自检。"""
    return await core_store.count_documents(COLLECTION_NAME, in_filters={"space_id": space_ids or ()})


async def page_chunks(
    *,
    space_id: int | None = None,
    doc_id: int | None = None,
    content: str | None = None,
    offset: int = 0,
    limit: int = 10,
    order_by: Sequence[tuple[str, str]] = (("doc_id", "asc"), ("chunk_index", "asc")),
) -> tuple[list[dict[str, Any]], int]:
    """分页浏览分片（运营页面用），返回 ``(命中列表, 总数)``。"""
    return await core_store.page_documents(
        COLLECTION_NAME,
        filters={"space_id": space_id, "doc_id": doc_id},
        like_filters={"content": content or ""},
        offset=offset,
        limit=limit,
        order_by=order_by,
    )


async def delete_chunks_by_ids(ids: Sequence[str]) -> int:
    """按分片主键删除（运营页面勾选删除用）。"""
    return await core_store.delete_documents(COLLECTION_NAME, ids=ids)
