"""知识写入的业务输入 / 回执结构（跨 publication / revision / chunk 三表）。

这些是「服务层入参」，不是 HTTP 契约模型：契约字段校验在 ``app/contract/entries.py``，
这里只承载已经校验过的数据，方便 service 保持纯业务、可单测。
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class EntryChunkData:
    """一个已批准的切片（不含向量，向量由服务层生成）。"""

    chunk_id: str
    seq: int
    content: str
    heading: str | None = None
    page_from: int | None = None
    page_to: int | None = None


@dataclass(frozen=True)
class EntryWriteData:
    """#03 首发 / #05 修订的完整写入载荷。"""

    request_id: str
    tenant_id: str
    user_id: str
    resource_type: str
    resource_id: str
    publication_id: str
    record_id: str
    version_id: str
    revision: int
    knowledge_no: str
    content_hash: str
    metadata: dict[str, Any] = field(default_factory=dict)
    chunks: tuple[EntryChunkData, ...] = ()
    base_record_id: str | None = None


@dataclass(frozen=True)
class EntryDeleteData:
    """#06 删除载荷。"""

    request_id: str
    tenant_id: str
    user_id: str
    resource_type: str
    resource_id: str
    publication_id: str
    record_id: str
    version_id: str
    revision: int
    reason: str


@dataclass(frozen=True)
class EntryReceipt:
    """#03 / #05 同步回执。"""

    request_id: str
    resource_type: str
    resource_id: str
    publication_id: str
    record_id: str
    version_id: str
    revision: int
    content_hash: str
    accepted_chunk_count: int
    searchable: bool


@dataclass(frozen=True)
class EntryDeleteReceipt:
    """#06 删除回执。"""

    request_id: str
    resource_type: str
    resource_id: str
    publication_id: str
    record_id: str
    version_id: str
    revision: int
    deleted_chunk_count: int
