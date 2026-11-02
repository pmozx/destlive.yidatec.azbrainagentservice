"""知识写入服务层（#03 首发 / #05 修订 / #06 整单删除）：校验 → 领域服务 → 回执投影。

本层只做契约相关的事：
1. ``content_hash`` 校验：按本侧固定序列化规则重算（Q9 尚未与 GSI 对齐）；
2. 路径 ``publication_id`` 与正文一致性（#05/#06）；
3. 领域异常 → 契约错误码：冲突 409 `STALE_REVISION`、不存在 404、越权 403、依赖失败 500。

字段与切片集合的结构校验（非空、chunk_id 唯一、seq 连续、页码合法）在 ``schema.py``。
"""

from typing import Any

from app.core.logger import logger
from app.modules.kb.publication.schema import (
    EntryChunkData,
    EntryDeleteData,
    EntryWriteData,
)
from app.modules.kb.publication.service import (
    EntryConflictError,
    EntryEmbeddingError,
    EntryNotFoundError,
    EntryScopeError,
    KbEntryService,
)

from ..errors import (
    CONTEXT_OUT_OF_SCOPE,
    INVALID_REQUEST,
    INTERNAL_ERROR,
    NOT_FOUND,
    STALE_REVISION,
    ContractApiError,
)
from .schema import EntryDeleteRequest, EntryWriteRequest, canonical_content_hash

_SERVICE = KbEntryService()


def verify_content_hash(payload: EntryWriteRequest) -> None:
    """整包摘要与切片集合必须一致；不一致说明调用方拿的不是同一份批准内容。"""
    expected = canonical_content_hash(payload.chunks)
    if payload.content_hash.strip() != expected:
        raise ContractApiError(
            STALE_REVISION,
            "content_hash 与本次切片集合不一致，请按审批通过的完整切片重新计算后提交。",
        )


def _ensure_path_publication(path_publication_id: str, payload_publication_id: str) -> None:
    if path_publication_id != payload_publication_id:
        raise ContractApiError(INVALID_REQUEST, "路径 publication_id 与正文 publication_id 不一致。")


def _to_write_data(payload: EntryWriteRequest) -> EntryWriteData:
    return EntryWriteData(
        request_id=payload.request_id,
        tenant_id=payload.tenant_id,
        user_id=payload.user_id,
        resource_type=payload.resource_type,
        resource_id=payload.resource_id,
        publication_id=payload.publication_id,
        record_id=payload.record_id,
        version_id=payload.version_id,
        revision=payload.revision,
        knowledge_no=payload.knowledge_no,
        content_hash=payload.content_hash,
        metadata=payload.metadata,
        chunks=tuple(
            EntryChunkData(
                chunk_id=chunk.chunk_id,
                seq=chunk.seq,
                content=chunk.content,
                heading=chunk.heading,
                page_from=chunk.page_from,
                page_to=chunk.page_to,
            )
            for chunk in payload.chunks
        ),
        base_record_id=payload.base_record_id,
    )


def _map_write_error(exc: Exception) -> ContractApiError:
    """领域异常 → 契约错误：service 不感知 HTTP，映射只在这里做。"""
    if isinstance(exc, EntryConflictError):
        return ContractApiError(STALE_REVISION, str(exc))
    if isinstance(exc, EntryNotFoundError):
        return ContractApiError(NOT_FOUND, str(exc))
    if isinstance(exc, EntryScopeError):
        return ContractApiError(CONTEXT_OUT_OF_SCOPE, str(exc))
    if isinstance(exc, EntryEmbeddingError):
        logger.error("知识写入向量化失败：{}", exc)
        return ContractApiError(INTERNAL_ERROR, "向量化依赖暂不可用，请稍后重试。")
    logger.exception("知识写入失败：{}", exc)
    return ContractApiError(INTERNAL_ERROR)


async def create_entry(payload: EntryWriteRequest) -> dict[str, Any]:
    """#03：首发 201；publication_id 已存在 → 409。"""
    verify_content_hash(payload)
    try:
        receipt = await _SERVICE.create(_to_write_data(payload))
    except Exception as exc:
        raise _map_write_error(exc) from exc
    return {
        "request_id": receipt.request_id,
        "resource_type": receipt.resource_type,
        "resource_id": receipt.resource_id,
        "publication_id": receipt.publication_id,
        "record_id": receipt.record_id,
        "version_id": receipt.version_id,
        "revision": receipt.revision,
        "success": True,
        "content_hash": receipt.content_hash,
        "accepted_chunk_count": receipt.accepted_chunk_count,
        "searchable": receipt.searchable,
    }


async def revise_entry(path_publication_id: str, payload: EntryWriteRequest) -> dict[str, Any]:
    """#05：路径/正文一致 + base_record_id 与 revision 单调校验，成功 200。"""
    _ensure_path_publication(path_publication_id, payload.publication_id)
    if payload.base_record_id is None:
        raise ContractApiError(INVALID_REQUEST, "#05 base_record_id 必填：用于并发基线校验。")
    verify_content_hash(payload)
    try:
        receipt = await _SERVICE.revise(_to_write_data(payload))
    except Exception as exc:
        raise _map_write_error(exc) from exc
    return {
        "request_id": receipt.request_id,
        "resource_type": receipt.resource_type,
        "resource_id": receipt.resource_id,
        "publication_id": receipt.publication_id,
        "record_id": receipt.record_id,
        "version_id": receipt.version_id,
        "revision": receipt.revision,
        "success": True,
        "content_hash": receipt.content_hash,
        "accepted_chunk_count": receipt.accepted_chunk_count,
        "searchable": receipt.searchable,
    }


async def delete_entry(path_publication_id: str, payload: EntryDeleteRequest) -> dict[str, Any]:
    """#06：删除当前生效快照，成功 200、`deleted=true`、`searchable=false`。"""
    _ensure_path_publication(path_publication_id, payload.publication_id)
    data = EntryDeleteData(
        request_id=payload.request_id,
        tenant_id=payload.tenant_id,
        user_id=payload.user_id,
        resource_type=payload.resource_type,
        resource_id=payload.resource_id,
        publication_id=payload.publication_id,
        record_id=payload.record_id,
        version_id=payload.version_id,
        revision=payload.revision,
        reason=payload.reason,
    )
    try:
        receipt = await _SERVICE.delete(data)
    except Exception as exc:
        raise _map_write_error(exc) from exc
    return {
        "request_id": receipt.request_id,
        "resource_type": receipt.resource_type,
        "resource_id": receipt.resource_id,
        "publication_id": receipt.publication_id,
        "record_id": receipt.record_id,
        "version_id": receipt.version_id,
        "revision": receipt.revision,
        "success": True,
        "deleted": True,
        "searchable": False,
    }
