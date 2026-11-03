"""知识写入 / 基线读取 Schema（03.1 新增 / 03.2 读取基线 / 03.3 修改 / 03.4 删除）。

``content_hash`` 的规则尚未与 GSI 对齐（Q9），``canonical_content_hash`` 即本侧口径；
对齐后如有变化，只改这一处并同步 ``app.scripts.contract.mock_gsi`` 的样例构造。

注意：v3 起 ``chunks[].chunk_id`` 变为可选（新增切片由 AI 分配），因此摘要只对
调用方双方都能确定的字段计算，**不含 chunk_id**，否则 GSI 无法在提交前算出同一个摘要。
"""

import hashlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.validator import ensure_non_blank

# content_hash 的固定序列化字段（顺序无关，JSON 键排序后拼接）；chunk_id 由 AI 分配，故不参与
_HASH_FIELDS: tuple[str, ...] = ("seq", "heading", "content", "page_from", "page_to")


class EntryChunkInput(BaseModel):
    """审批通过后的一个完整切片（向量由 AI 生成，请求里不传）。"""

    model_config = ConfigDict(extra="ignore")

    chunk_id: str | None = Field(
        default=None,
        description="已有切片沿用 AI 返回的原 ID；新增切片省略，由 AI 分配",
    )
    seq: int = Field(ge=1, description="切片顺序，从 1 开始且连续")
    heading: str | None = Field(default=None, description="切片小标题")
    content: str = Field(description="完整切片正文")
    page_from: int | None = Field(default=None, ge=1, description="起始页")
    page_to: int | None = Field(default=None, ge=1, description="结束页")

    @field_validator("chunk_id", "content")
    @classmethod
    def _required_text(cls, value: str | None) -> str | None:
        return ensure_non_blank(value) if value is not None else None


class EntryWriteRequest(BaseModel):
    """03.1 / 03.3 请求正文（字段与手册 §六.03.1 / §六.03.3 一致）。"""

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
    base_record_id: str | None = Field(default=None, description="03.3 必填：进入修订时的原快照 ID")

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
        return ensure_non_blank(value)

    @field_validator("resource_type")
    @classmethod
    def _resource_type_enum(cls, value: str) -> str:
        if value not in ("document", "faq"):
            raise ValueError("resource_type 只支持 document / faq")
        return value

    @field_validator("base_record_id")
    @classmethod
    def _base_record_not_blank(cls, value: str | None) -> str | None:
        return ensure_non_blank(value) if value is not None else None

    @field_validator("chunks")
    @classmethod
    def _chunk_set_shape(cls, value: list[EntryChunkInput]) -> list[EntryChunkInput]:
        """切片集合是「全量替换」：必须非空、已传的 chunk_id 唯一、seq 从 1 连续。

        ``chunk_id`` 自 v3 起可选（新增切片由 AI 分配），所以只校验调用方实际传入的 ID。
        """
        if not value:
            raise ValueError("chunks 不能为空：写入必须是本次完整切片集合。")
        chunk_ids = [chunk.chunk_id for chunk in value if chunk.chunk_id is not None]
        if len(set(chunk_ids)) != len(chunk_ids):
            raise ValueError("chunks 内 chunk_id 重复，同一次切片集内必须唯一。")
        seqs = sorted(chunk.seq for chunk in value)
        if seqs != list(range(1, len(seqs) + 1)):
            raise ValueError("chunks.seq 必须从 1 开始连续编号。")
        return value


class EntryDeleteRequest(BaseModel):
    """03.4 请求正文。"""

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
        return ensure_non_blank(value)

    @field_validator("resource_type")
    @classmethod
    def _resource_type_enum(cls, value: str) -> str:
        if value not in ("document", "faq"):
            raise ValueError("resource_type 只支持 document / faq")
        return value


def canonical_content_hash(chunks: list[EntryChunkInput] | list[dict[str, Any]]) -> str:
    """按固定规则计算整包摘要：按 ``seq`` 排序后对确定字段做 JSON（键排序、紧凑分隔符）。"""
    rows: list[dict[str, Any]] = []
    for chunk in chunks:
        item = chunk.model_dump() if isinstance(chunk, BaseModel) else dict(chunk)
        rows.append({field: item.get(field) for field in _HASH_FIELDS})
    rows.sort(key=lambda row: int(row["seq"]))
    serialized = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class EntryWriteReceipt(BaseModel):
    """03.1 / 03.3 写入回执。"""

    request_id: str
    resource_type: str
    resource_id: str
    publication_id: str
    record_id: str
    version_id: str
    revision: int
    success: bool
    content_hash: str
    accepted_chunk_count: int
    searchable: bool


class EntryDeleteReceipt(BaseModel):
    """03.4 删除回执。"""

    request_id: str
    resource_type: str
    resource_id: str
    publication_id: str
    record_id: str
    version_id: str
    revision: int
    success: bool
    deleted: bool
    searchable: bool


class EntryQueryRequest(BaseModel):
    """03.2 读取知识修订基线请求正文（字段与手册 §六.03.2 一致）。"""

    model_config = ConfigDict(extra="ignore")

    request_id: str
    tenant_id: str
    user_id: str
    record_id: str = Field(description="要读取的确切生效快照 ID")
    cursor: str | None = Field(default=None, description="切片分页游标，首次省略")
    limit: int = Field(default=100, ge=1, le=500, description="每页切片数，默认 100，1–500")

    @field_validator("request_id", "tenant_id", "user_id", "record_id")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return ensure_non_blank(value)

    @field_validator("cursor")
    @classmethod
    def _cursor_not_blank(cls, value: str | None) -> str | None:
        return ensure_non_blank(value) if value is not None else None


class EntryBaselineChunk(BaseModel):
    """03.2 响应中的正式切片（`chunk_id` 必返）。"""

    chunk_id: str
    seq: int
    heading: str | None = None
    content: str
    page_from: int | None = None
    page_to: int | None = None


class EntryQueryResponse(BaseModel):
    """03.2 读取知识修订基线响应。"""

    request_id: str
    resource_type: str
    resource_id: str
    publication_id: str
    record_id: str
    version_id: str
    revision: int
    knowledge_no: str
    metadata: dict[str, Any]
    content_hash: str
    chunks: list[EntryBaselineChunk]
    searchable: bool
    next_cursor: str | None = None

