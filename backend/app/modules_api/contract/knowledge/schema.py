"""知识写入请求 Schema 与整包摘要口径（#03 首发 / #05 修订 / #06 整单删除）。

``content_hash`` 的规则尚未与 GSI 对齐（Q9），``canonical_content_hash`` 即本侧口径；
对齐后如有变化，只改这一处并同步 ``app.scripts.contract.mock_gsi`` 的样例构造。
"""

import hashlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.validator import ensure_non_blank

# content_hash 的固定序列化字段（顺序无关，JSON 键排序后拼接）
_HASH_FIELDS: tuple[str, ...] = ("chunk_id", "seq", "heading", "content", "page_from", "page_to")


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
        return ensure_non_blank(value)

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
        return ensure_non_blank(value)

    @field_validator("resource_type")
    @classmethod
    def _resource_type_enum(cls, value: str) -> str:
        if value not in ("document", "faq"):
            raise ValueError("resource_type 只支持 document / faq")
        return value


def canonical_content_hash(chunks: list[EntryChunkInput] | list[dict[str, Any]]) -> str:
    """按固定规则计算整包摘要：按 ``seq`` 排序后对 6 个字段做 JSON（键排序、紧凑分隔符）。"""
    rows: list[dict[str, Any]] = []
    for chunk in chunks:
        item = chunk.model_dump() if isinstance(chunk, BaseModel) else dict(chunk)
        rows.append({field: item.get(field) for field in _HASH_FIELDS})
    rows.sort(key=lambda row: int(row["seq"]))
    serialized = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class EntryWriteReceipt(BaseModel):
    """`#03` / `#05` 写入回执。"""

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
    """`#06` 删除回执。"""

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

