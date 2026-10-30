"""知识写入契约层（#03 首发 / #05 修订 / #06 整单删除）：校验 → service → 回执投影。

本层只做契约相关的事：
1. 字段与切片集合校验（非空、chunk_id 唯一、seq 从 1 连续、页码合法）；
2. `content_hash` 校验：按本侧固定序列化规则重算（Q9 尚未与 GSI 对齐，规则见 ``canonical_content_hash``）；
3. 路径 ``publication_id`` 与正文一致性（#05/#06）；
4. 领域异常 → 契约错误码：冲突 409 `STALE_REVISION`、不存在 404、越权 403、依赖失败 500。
"""

import hashlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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

from .errors import (
    CONTEXT_OUT_OF_SCOPE,
    INVALID_REQUEST,
    INTERNAL_ERROR,
    NOT_FOUND,
    STALE_REVISION,
    ContractApiError,
)

_SERVICE = KbEntryService()

# content_hash 的固定序列化字段（顺序无关，JSON 键排序后拼接）
_HASH_FIELDS: tuple[str, ...] = ("chunk_id", "seq", "heading", "content", "page_from", "page_to")


def _require_non_blank(value: str) -> str:
    if not value or not value.strip():
        raise ValueError("不能为空或仅包含空白字符")
    return value


class EntryChunkInput(BaseModel):
    """审批通过后的一个完整切片（向量由 AI 生成，请求里不传）。"""

    model_config = ConfigDict(extra="ignore")

    chunk_id: str = Field(description="GSI 生成的切片 ID，本次集合内唯一")
    seq: int = Field(ge=1, description="切片顺序，从 1 开始且连续")
    heading: str | None = Field(default=None, description="切片小标题")
    content: str = Field(description="完整切片正文")
    page_from: int | None = Field(default=None, ge=1, description="起始页")
    page_to: int | None = Field(default=None, ge=1, description="结束页")

    @field_validator("chunk_id", "content")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return _require_non_blank(value)

    @model_validator(mode="after")
    def _page_range(self) -> "EntryChunkInput":
        if self.page_from is not None and self.page_to is not None and self.page_to < self.page_from:
            raise ValueError("page_to 不能小于 page_from")
        return self


class EntryWriteRequest(BaseModel):
    """#03 / #05 请求正文（字段与手册 §六.03.1 / §六.03.3 一致）。"""

    model_config = ConfigDict(extra="ignore")

    request_id: str
    tenant_id: str
    user_id: str
    resource_type: str
    resource_id: str
    publication_id: str
    record_id: str
    version_id: str
    revision: int = Field(ge=1)
    knowledge_no: str
    metadata: dict[str, Any]
    content_hash: str
    chunks: list[EntryChunkInput]
    base_record_id: str | None = Field(default=None, description="#05 必填：进入修订时的原快照 ID")

    @field_validator(
        "request_id",
        "tenant_id",
        "user_id",
        "resource_id",
        "publication_id",
        "record_id",
        "version_id",
        "knowledge_no",
        "content_hash",
    )
    @classmethod
    def _required_text(cls, value: str) -> str:
        return _require_non_blank(value)

    @field_validator("resource_type")
    @classmethod
    def _resource_type_enum(cls, value: str) -> str:
        if value not in ("document", "faq"):
            raise ValueError("resource_type 只支持 document / faq")
        return value

    @field_validator("base_record_id")
    @classmethod
    def _base_record_not_blank(cls, value: str | None) -> str | None:
        return _require_non_blank(value) if value is not None else None

    @field_validator("chunks")
    @classmethod
    def _chunk_set_shape(cls, value: list[EntryChunkInput]) -> list[EntryChunkInput]:
        """切片集合是「全量替换」：必须非空、chunk_id 唯一、seq 从 1 连续。"""
        if not value:
            raise ValueError("chunks 不能为空：写入必须是本次完整切片集合。")
        chunk_ids = [chunk.chunk_id for chunk in value]
        if len(set(chunk_ids)) != len(chunk_ids):
            raise ValueError("chunks 内 chunk_id 重复，同一次切片集内必须唯一。")
        seqs = sorted(chunk.seq for chunk in value)
        if seqs != list(range(1, len(seqs) + 1)):
            raise ValueError("chunks.seq 必须从 1 开始连续编号。")
        return value


class EntryDeleteRequest(BaseModel):
    """#06 请求正文。"""

    model_config = ConfigDict(extra="ignore")

    request_id: str
    tenant_id: str
    user_id: str
    resource_type: str
    resource_id: str
    publication_id: str
    record_id: str
    version_id: str
    revision: int = Field(ge=1)
    reason: str

    @field_validator(
        "request_id",
        "tenant_id",
        "user_id",
        "resource_id",
        "publication_id",
        "record_id",
        "version_id",
        "reason",
    )
    @classmethod
    def _required_text(cls, value: str) -> str:
        return _require_non_blank(value)

    @field_validator("resource_type")
    @classmethod
    def _resource_type_enum(cls, value: str) -> str:
        if value not in ("document", "faq"):
            raise ValueError("resource_type 只支持 document / faq")
        return value


def canonical_content_hash(chunks: list[EntryChunkInput] | list[dict[str, Any]]) -> str:
    """按固定规则计算整包摘要：按 ``seq`` 排序后对 6 个字段做 JSON（键排序、紧凑分隔符）。

    规则未在契约中定稿（Q9），此实现即本侧口径；与 GSI 对齐后如有变化，只改这一处并同步
    ``mock_gsi`` 的样例构造。
    """
    rows: list[dict[str, Any]] = []
    for chunk in chunks:
        item = chunk.model_dump() if isinstance(chunk, BaseModel) else dict(chunk)
        rows.append({field: item.get(field) for field in _HASH_FIELDS})
    rows.sort(key=lambda row: int(row["seq"]))
    serialized = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()


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
