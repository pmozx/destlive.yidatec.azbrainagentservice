"""#03 首次发布写入：切片 → bge-m3 向量化 → 覆盖写入 → 回执。

口径：
1. 只做「首次发布」路径：`publication_id` 已存在由路由层回 409，修订/删除属 T3.2/T3.3；
2. 原文与向量同事务写入：向量化失败整单回滚，绝不允许「有正文没向量」的半成品入库
   （否则检索看起来通了、实际永远召回不到）；
3. `content_hash` 原样存 GSI 给的值，不在 AI 侧重算 —— 双方「固定序列化规则」尚未对齐，
   重算只会制造两边永远不一致的假冲突（已登记 T0.4 疑点）；
4. `searchable` = 向量已就绪且切片非空；契约明说它**不是**用户访问许可。
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clients.embedding import EmbeddingClient
from app.core.logger import logger

from . import store
from .errors import INVALID_REQUEST, PocApiError
from .metrics import StageTimer
from .schemas import KnowledgeEntryRequest


def _validate_chunk_set(payload: KnowledgeEntryRequest) -> None:
    """契约要求 chunk_id 在本次切片集中唯一、seq 从 1 开始连续编号；重复只会撞唯一键变 500。"""
    if not payload.chunks:
        raise PocApiError(INVALID_REQUEST, "chunks 为空：首次发布必须提交完整切片集合。", status_code=400)
    chunk_ids = [chunk.chunk_id for chunk in payload.chunks]
    if len(set(chunk_ids)) != len(chunk_ids):
        raise PocApiError(INVALID_REQUEST, "chunks 内 chunk_id 重复，同一次切片集内必须唯一。", status_code=400)
    seqs = sorted(chunk.seq for chunk in payload.chunks)
    if seqs != list(range(1, len(seqs) + 1)):
        raise PocApiError(INVALID_REQUEST, "chunks.seq 必须从 1 开始连续编号。", status_code=400)


def _chunk_rows(
    payload: KnowledgeEntryRequest,
    vectors: list[list[float]],
    *,
    title: str,
) -> list[dict[str, Any]]:
    """拼切片行：把发布元数据冗余进每一行，检索时单表出结果（见 `models.py` 取舍 2）。

    不含 `publication_id`：它由 `store.replace_chunks` 统一传入（整批属于同一发布单元），
    两边都传会变成重复关键字参数。
    """
    return [
        {
            "record_id": payload.record_id,
            "chunk_id": chunk.chunk_id,
            "seq": chunk.seq,
            "heading": chunk.heading,
            "content": chunk.content,
            "page_from": chunk.page_from,
            "page_to": chunk.page_to,
            "resource_type": payload.resource_type,
            "resource_id": payload.resource_id,
            "version_id": payload.version_id,
            "revision": payload.revision,
            "knowledge_no": payload.knowledge_no,
            "title": title,
            "embedding": vector,
        }
        for chunk, vector in zip(payload.chunks, vectors, strict=True)
    ]


async def write_publication(
    db: AsyncSession,
    payload: KnowledgeEntryRequest,
    *,
    body_hash: str,
    timer: StageTimer | None = None,
) -> dict[str, Any]:
    """写入一份已批准的切片集合，返回契约回执（`searchable` 为真才表示可被召回）。"""
    timer = timer or StageTimer()
    _validate_chunk_set(payload)

    meta = payload.metadata.model_dump(mode="json", exclude_none=True)
    title = str(meta.get("title") or "").strip() or payload.knowledge_no

    async with timer.stage("embed"):
        vectors = await EmbeddingClient().embed_texts([chunk.content for chunk in payload.chunks])
    rows = _chunk_rows(payload, vectors, title=title)

    async with timer.stage("persist"):
        await store.insert_publication(
            db,
            {
                "request_id": payload.request_id,
                "body_hash": body_hash,
                "tenant_id": payload.tenant_id,
                "user_id": payload.user_id,
                "resource_type": payload.resource_type,
                "resource_id": payload.resource_id,
                "publication_id": payload.publication_id,
                "record_id": payload.record_id,
                "version_id": payload.version_id,
                "revision": payload.revision,
                "knowledge_no": payload.knowledge_no,
                "title": title,
                "content_hash": payload.content_hash,
                "chunk_count": len(rows),
                "meta": meta,
            },
        )
        inserted = await store.replace_chunks(db, payload.publication_id, rows)

    receipt: dict[str, Any] = {
        "request_id": payload.request_id,
        "resource_type": payload.resource_type,
        "resource_id": payload.resource_id,
        "publication_id": payload.publication_id,
        "record_id": payload.record_id,
        "version_id": payload.version_id,
        "revision": payload.revision,
        "success": True,
        "content_hash": payload.content_hash,
        "accepted_chunk_count": inserted,
        "searchable": bool(inserted and len(vectors) == inserted),
    }
    logger.info(
        "POC #03 写入完成：publication_id={} revision={} chunks={} 耗时={}ms",
        payload.publication_id,
        payload.revision,
        inserted,
        timer.total_ms,
    )
    return receipt
