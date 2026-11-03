"""知识发布与检索服务层（03.1 / 03.2 / 03.3 / 03.4 / #07）：校验 → 领域服务 → 回执投影。

本层只做契约相关的事：
1. ``content_hash`` 校验：按本侧固定序列化规则重算（Q9 尚未与 GSI 对齐）；
2. 路径 ``publication_id`` 与正文一致性（03.3/03.4）；
3. **切片 ID 分配**：新增切片未传 ``chunk_id`` 时按 ``record_id:seq`` 确定性生成，
   既有切片沿用调用方传入的 ID（AI 校验归属由领域层完成）；
4. ``cursor`` 解析：游标是不透明字符串，本侧按 ``seq`` 实现（``seq > cursor``）；
5. 领域异常 → 契约错误码：冲突 409 `STALE_REVISION`、不存在 404、越权 403、依赖失败 500；
6. #07 检索：``mode`` 分发与「无证据」语义；权限数据不可用 → 503（绝不降级全库）。

字段与切片集合的结构校验（非空、chunk_id 唯一、seq 连续、页码合法）在 ``schema.py``。
"""

from typing import Any

from fastapi import status

from app.core.logger import logger
from app.modules.kb.publication.schema import (
    BaselineQuery,
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
from app.modules.kb.retrieval.schema import KeywordQuery
from app.modules.kb.retrieval.service import (
    KbRetrievalService,
    RetrievalAuthorizationUnavailable,
)

from ..errors import (
    AUTHORIZATION_UNAVAILABLE,
    CONTEXT_OUT_OF_SCOPE,
    INVALID_REQUEST,
    INTERNAL_ERROR,
    NOT_FOUND,
    STALE_REVISION,
    ContractApiError,
)
from .schema import (
    EntryDeleteRequest,
    EntryQueryRequest,
    EntryWriteRequest,
    SearchRequest,
    canonical_content_hash,
)

_SERVICE = KbEntryService()
_RETRIEVAL = KbRetrievalService()


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
        chunks=_entry_chunks(payload),
        base_record_id=payload.base_record_id,
    )


def _entry_chunks(payload: EntryWriteRequest) -> tuple[EntryChunkData, ...]:
    """切片投影：未传 ``chunk_id`` 的新增切片按 ``record_id:seq`` 分配，已有切片沿用原 ID。

    规则必须确定性（同请求重试不能换 ID），与 03.2 示例 ``record-demo-001:1`` 一致。
    """
    return tuple(
        EntryChunkData(
            chunk_id=chunk.chunk_id or f"{payload.record_id}:{chunk.seq}",
            seq=chunk.seq,
            content=chunk.content,
            heading=chunk.heading,
            page_from=chunk.page_from,
            page_to=chunk.page_to,
        )
        for chunk in payload.chunks
    )


def _after_seq(cursor: str | None) -> int:
    """03.2 游标解析：游标由本侧签发（上一页最后一个切片的 ``seq``），非法游标 400。"""
    if cursor is None:
        return 0
    try:
        value = int(cursor)
    except (TypeError, ValueError) as exc:
        raise ContractApiError(INVALID_REQUEST, "cursor 无效：请原样回传上一页的 next_cursor。") from exc
    if value < 0:
        raise ContractApiError(INVALID_REQUEST, "cursor 无效：请原样回传上一页的 next_cursor。")
    return value


def _map_domain_error(exc: Exception) -> ContractApiError:
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
    logger.exception("知识发布处理失败：{}", exc)
    return ContractApiError(INTERNAL_ERROR)


async def create_entry(payload: EntryWriteRequest) -> dict[str, Any]:
    """03.1：首发 201；publication_id 已存在 → 409。"""
    verify_content_hash(payload)
    try:
        receipt = await _SERVICE.create(_to_write_data(payload))
    except Exception as exc:
        raise _map_domain_error(exc) from exc
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


async def query_entry(path_publication_id: str, payload: EntryQueryRequest) -> dict[str, Any]:
    """03.2：只读已批准快照，支持历史 ``record_id`` 与 ``seq`` 游标分页。"""
    try:
        baseline = await _SERVICE.read_baseline(
            BaselineQuery(
                publication_id=path_publication_id,
                tenant_id=payload.tenant_id,
                user_id=payload.user_id,
                record_id=payload.record_id,
                after_seq=_after_seq(payload.cursor),
                limit=payload.limit,
            )
        )
    except Exception as exc:
        raise _map_domain_error(exc) from exc
    return {
        "request_id": payload.request_id,
        "resource_type": baseline.resource_type,
        "resource_id": baseline.resource_id,
        "publication_id": baseline.publication_id,
        "record_id": baseline.record_id,
        "version_id": baseline.version_id,
        "revision": baseline.revision,
        "knowledge_no": baseline.knowledge_no,
        "metadata": baseline.metadata,
        "content_hash": baseline.content_hash,
        "chunks": [
            {
                "chunk_id": chunk.chunk_id,
                "seq": chunk.seq,
                "heading": chunk.heading,
                "content": chunk.content,
                "page_from": chunk.page_from,
                "page_to": chunk.page_to,
            }
            for chunk in baseline.chunks
        ],
        "searchable": baseline.searchable,
        "next_cursor": baseline.next_cursor,
    }


async def revise_entry(path_publication_id: str, payload: EntryWriteRequest) -> dict[str, Any]:
    """03.3：路径/正文一致 + base_record_id 与 revision 单调校验，成功 200。"""
    _ensure_path_publication(path_publication_id, payload.publication_id)
    if payload.base_record_id is None:
        raise ContractApiError(INVALID_REQUEST, "03.3 base_record_id 必填：用于并发基线校验。")
    verify_content_hash(payload)
    try:
        receipt = await _SERVICE.revise(_to_write_data(payload))
    except Exception as exc:
        raise _map_domain_error(exc) from exc
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
    """03.4：删除指定 ``record_id`` 的正文与向量，成功 200、`deleted=true`、`searchable=false`。"""
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
        raise _map_domain_error(exc) from exc
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


# ── #07 关键词 / 自然语言检索 ────────────────────────────────────────


async def search_knowledge_base(payload: SearchRequest) -> dict[str, Any]:
    """#07：keyword 模式（T4.1）已实现；natural_language 待 T4.2 交付。

    自然语言通道明确回 501，而不是回空结果：空 ``hits`` 在契约里意味着
    「库里确实没有依据」（``no_evidence=true``），拿它冒充未实现会把联调方引到错的方向。

    授权下推在领域层完成（先算可检索发布单元集合，再在 SQL 里过滤）：
    空集合直接返回空结果；权限数据不可用则 503，不做降级检索。
    """
    if payload.mode == "natural_language":
        raise ContractApiError(
            INTERNAL_ERROR,
            "自然语言检索（向量召回 + RRF + rerank）由 T4.2 交付，当前仅支持 mode=keyword。",
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            retryable=False,
            request_id=payload.request_id,
        )

    try:
        result = await _RETRIEVAL.keyword_search(
            KeywordQuery(
                query=payload.query,
                tenant_id=payload.tenant_id,
                user_id=payload.user_id,
                limit=payload.limit,
            )
        )
    except RetrievalAuthorizationUnavailable as exc:
        raise ContractApiError(AUTHORIZATION_UNAVAILABLE, str(exc)) from exc

    return {
        "request_id": payload.request_id,
        "hits": [
            {
                "resource_type": hit.resource_type,
                "resource_id": hit.resource_id,
                "publication_id": hit.publication_id,
                "record_id": hit.record_id,
                "version_id": hit.version_id,
                "revision": hit.revision,
                "knowledge_no": hit.knowledge_no,
                "chunk_id": hit.chunk_id,
                "title": hit.title,
                "snippet": hit.snippet,
                "page_from": hit.page_from,
                "page_to": hit.page_to,
                "score": hit.score,
            }
            for hit in result.hits
        ],
        "returned_count": len(result.hits),
        "no_evidence": result.no_evidence,
    }
