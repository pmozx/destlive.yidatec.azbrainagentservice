"""发布单元 Schema：领域层数据结构 + 管理端 HTTP 契约。

合并原 ``knowledge`` 子模块后，本文件统一承载：
- **领域层 dataclass**（§一）：服务层入参/回执，不依赖 Pydantic，方便单测；
- **管理端 Pydantic Schema**（§二）：HTTP 请求/响应模型，含校验规则。
"""

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.core.base_schema import BaseQueryParam, BaseSchema
from app.core.validator import DateTimeStr, ensure_non_blank

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# §一  领域层 dataclass（契约写入/删除/回执/基线查询）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# content_hash 的固定序列化字段（顺序无关，JSON 键排序后拼接）；chunk_id 由 AI 分配，故不参与。
# 契约 03.1/03.3 与管理端正文写入共用这一份口径：各写一版会让同一份切片算出不同摘要。
ENTRY_HASH_FIELDS: tuple[str, ...] = ("seq", "heading", "content", "page_from", "page_to")


def canonical_content_hash(chunks: Iterable[Any]) -> str:
    """按固定规则计算整包摘要：按 ``seq`` 排序后对确定字段做 JSON（键排序、紧凑分隔符）。"""
    rows: list[dict[str, Any]] = []
    for chunk in chunks:
        item = chunk.model_dump() if isinstance(chunk, BaseModel) else dict(chunk)
        rows.append({name: item.get(name) for name in ENTRY_HASH_FIELDS})
    rows.sort(key=lambda row: int(row["seq"]))
    serialized = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class EntryChunkData:
    """一个已批准的切片（不含向量，向量由服务层生成）。

    ``chunk_id`` 为空表示新增切片：由 :class:`KbEntryService` 按 ``record_id:seq``
    确定性分配（同一次写入重试不能换 ID），调用方无需自己造 ID。
    """

    chunk_id: str | None
    seq: int
    content: str
    heading: str | None = None
    page_from: int | None = None
    page_to: int | None = None


@dataclass(frozen=True)
class EntryWriteData:
    """03.1 首发 / 03.3 修订的完整写入载荷。"""

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
    """03.4 删除载荷。"""

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
    """03.1 / 03.3 同步回执。"""

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
class EntryDeactivateData:
    """D05 停用载荷（通用接口，不带 resource_type / resource_id）。"""

    request_id: str
    tenant_id: str
    user_id: str
    publication_id: str
    record_id: str
    revision: int
    reason: str


@dataclass(frozen=True)
class EntryDeactivateReceipt:
    """D05 停用回执（固定 deactivated=True / searchable=False）。"""

    request_id: str
    publication_id: str
    record_id: str
    revision: int
    deactivated: bool = True
    searchable: bool = False


@dataclass(frozen=True)
class EntryDeleteReceipt:
    """03.4 删除回执。"""

    request_id: str
    resource_type: str
    resource_id: str
    publication_id: str
    record_id: str
    version_id: str
    revision: int
    deleted_chunk_count: int


@dataclass(frozen=True)
class BaselineQuery:
    """03.2 读取知识修订基线的入参。"""

    publication_id: str
    tenant_id: str
    user_id: str
    record_id: str
    after_seq: int = 0
    limit: int = 100


@dataclass(frozen=True)
class BaselineChunk:
    """03.2 返回的一个正式切片。"""

    chunk_id: str
    seq: int
    content: str
    heading: str | None = None
    page_from: int | None = None
    page_to: int | None = None


@dataclass(frozen=True)
class BaselineData:
    """03.2 读取知识修订基线的结果（发布单元元信息 + 当前页切片）。"""

    publication_id: str
    resource_type: str
    resource_id: str
    record_id: str
    version_id: str
    revision: int
    knowledge_no: str
    metadata: dict[str, Any]
    content_hash: str
    chunks: tuple[BaselineChunk, ...]
    searchable: bool
    next_cursor: str | None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# §二  管理端 Pydantic Schema（HTTP 请求/响应）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# 知识类型与发布单元状态的白名单（与模型 CheckConstraint 保持同一口径）
RESOURCE_TYPES: tuple[str, ...] = ("document", "faq")
# 状态值常量化：crud（判生效指针）与 service（判写链路前置）必须认同一个取值，
# 字面量散落两处时，一边改了一边漏改会变成「只在停用后失效」的隐性 bug
PUBLICATION_ACTIVE = "active"
PUBLICATION_INACTIVE = "inactive"
PUBLICATION_DELETED = "deleted"
PUBLICATION_STATUSES: tuple[str, ...] = (PUBLICATION_ACTIVE, PUBLICATION_INACTIVE, PUBLICATION_DELETED)


def _normalize_tags(tags: list[str]) -> list[str]:
    """标签归一：去首尾空白、去空值、保序去重，避免前端重复标签落库。"""
    result: list[str] = []
    for tag in tags:
        text = tag.strip()
        if text and text not in result:
            result.append(text)
    return result


class KbPublicationCreateSchema(BaseModel):
    """新增发布单元：同时创建契约指针与管理端展示字段。"""

    publication_id: str = Field(..., min_length=1, max_length=64, description="发布单元 ID（全局唯一）")
    resource_type: Literal["document", "faq"] = Field(default="document", description="知识类型")
    tenant_id: str | None = Field(default=None, max_length=64, description="租户 ID")
    resource_id: str | None = Field(default=None, max_length=64, description="原始资源 ID")
    title: str = Field(..., min_length=1, max_length=200, description="标题")
    category: str | None = Field(default=None, max_length=64, description="知识分类")
    summary: str | None = Field(default=None, description="内容摘要")
    tags: list[str] = Field(default_factory=list, description="标签")
    owner: str | None = Field(default=None, max_length=64, description="维护人")

    @field_validator("publication_id", "title")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        return ensure_non_blank(value)

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, value: list[str]) -> list[str]:
        return _normalize_tags(value)


class KbPublicationUpdateSchema(BaseModel):
    """编辑发布单元：只更新调用方实际传入的字段（显式传 null 表示清空可空字段）。"""

    resource_type: Literal["document", "faq"] | None = Field(default=None, description="知识类型")
    tenant_id: str | None = Field(default=None, max_length=64, description="租户 ID")
    resource_id: str | None = Field(default=None, max_length=64, description="原始资源 ID")
    title: str | None = Field(default=None, min_length=1, max_length=200, description="标题")
    category: str | None = Field(default=None, max_length=64, description="知识分类")
    summary: str | None = Field(default=None, description="内容摘要")
    tags: list[str] | None = Field(default=None, description="标签")
    owner: str | None = Field(default=None, max_length=64, description="维护人")

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str | None) -> str | None:
        return ensure_non_blank(value) if value is not None else None

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, value: list[str] | None) -> list[str] | None:
        return _normalize_tags(value) if value is not None else None


class KbPublicationStatusSchema(BaseModel):
    """上下线：只改发布单元状态，不动内容与版本。"""

    status: Literal["active", "inactive", "deleted"] = Field(..., description="active/inactive/deleted")


class KbPublicationOutSchema(BaseSchema):
    """发布单元列表响应。"""

    publication_id: str = Field(..., description="发布单元 ID")
    title: str = Field(..., description="标题")
    category: str | None = Field(default=None, description="知识分类")
    summary: str | None = Field(default=None, description="内容摘要")
    tags: list[str] = Field(default_factory=list, description="标签")
    owner: str | None = Field(default=None, description="维护人")
    resource_type: str | None = Field(default=None, description="知识类型")
    tenant_id: str | None = Field(default=None, description="租户 ID")
    resource_id: str | None = Field(default=None, description="原始资源 ID")
    current_record_id: str | None = Field(default=None, description="当前生效记录")
    current_revision: int | None = Field(default=None, description="当前生效版本号")
    last_synced_at: DateTimeStr | None = Field(default=None, description="指针同步时间")
    status: str | None = Field(default=None, description="发布单元状态")


class KbPublicationDetailOutSchema(KbPublicationOutSchema):
    """发布单元详情响应（补充版本数/切片数，便于详情页首屏展示）。"""

    version_count: int = Field(default=0, description="版本快照数")
    chunk_count: int = Field(default=0, description="切片总数")


class KbPublicationParseJobBriefOutSchema(BaseModel):
    """疑似同源的解析任务（只读线索，不构成强关联）。"""

    job_id: str = Field(..., description="契约 job_id（= parse_job.uuid）")
    status: str = Field(..., description="queued/running/succeeded/failed")
    progress: int = Field(default=0, description="进度 0–100")
    resource_type: str = Field(default="", description="document/faq")
    created_time: DateTimeStr | None = Field(default=None, description="任务创建时间")
    matched_by: str = Field(default="file_meta.doc_id", description="匹配依据（只支持 doc_id）")


class KbPublicationSourceOutSchema(BaseModel):
    """发布单元来源与关联（只读溯源）。

    发布单元与解析任务之间没有库级关联（见 ``02-数据模型与表关系.md`` §1.2「内容与作业分离」），
    这里只回「可以核对得上的线索」：写入渠道 + 按 ``file_meta.doc_id`` 反查的解析任务。
    """

    publication_id: str = Field(..., description="发布单元 ID")
    resource_type: str | None = Field(default=None, description="document / faq")
    resource_id: str | None = Field(default=None, description="GSI 原始资源 ID（03.1 的 resource_id）")
    tenant_id: str | None = Field(default=None, description="租户 ID")
    status: str | None = Field(default=None, description="发布单元状态")
    current_record_id: str | None = Field(default=None, description="当前生效记录")
    current_revision: int | None = Field(default=None, description="当前生效版本号")
    write_channel: str = Field(..., description="admin（管理台写入）/ contract（GSI 契约写入）/ pending（无快照）")
    parse_jobs: list[KbPublicationParseJobBriefOutSchema] = Field(
        default_factory=list, description="疑似同源的解析任务（按创建时间倒序，最多 20 条）"
    )
    hint: str = Field(..., description="口径提醒：线索来源与局限性")


class KbPublicationQueryParam(BaseQueryParam):
    """发布单元查询参数（``q`` 标记决定 CRUD 层的比较方式）。"""

    publication_id: str | None = Field(None, description="发布单元 ID", json_schema_extra={"q": "like"})
    title: str | None = Field(None, description="标题", json_schema_extra={"q": "like"})
    category: str | None = Field(None, description="知识分类", json_schema_extra={"q": "eq"})
    owner: str | None = Field(None, description="维护人", json_schema_extra={"q": "like"})
    resource_type: str | None = Field(None, description="知识类型", json_schema_extra={"q": "eq"})
    resource_id: str | None = Field(None, description="GSI 原始资源 ID（按解析任务 doc_id 反查时用）", json_schema_extra={"q": "eq"})
    status: str | None = Field(None, description="发布单元状态", json_schema_extra={"q": "eq"})


class KbPublicationRevisionOutSchema(BaseSchema):
    """版本快照列表响应（不返回 metadata，避免列表数据膨胀）。"""

    publication_id: str = Field(..., description="发布单元 ID")
    record_id: str = Field(..., description="版本记录 ID")
    version_id: str | None = Field(default=None, description="契约版本 ID")
    knowledge_no: str | None = Field(default=None, description="知识编号")
    base_record_id: str | None = Field(default=None, description="基线记录")
    revision: int = Field(..., description="版本号")
    content_hash: str | None = Field(default=None, description="内容指纹")
    chunk_count: int = Field(default=0, description="切片数")
    searchable: bool = Field(default=False, description="是否可检索")
    status: str = Field(..., description="online/archived")


class KbPublicationRevisionDetailOutSchema(KbPublicationRevisionOutSchema):
    """版本快照详情响应（含契约透传元数据）。"""

    # 模型属性名是 metadata_（metadata 被 Declarative 占用），对外仍按契约字段名输出
    metadata: dict[str, Any] | None = Field(
        default=None,
        validation_alias="metadata_",
        serialization_alias="metadata",
        description="契约透传元数据",
    )


class KbPublicationChunkOutSchema(BaseSchema):
    """切片列表响应（不含向量，向量只在检索链路使用）。"""

    publication_id: str = Field(..., description="发布单元 ID")
    record_id: str = Field(..., description="版本记录 ID")
    revision: int = Field(..., description="版本号")
    chunk_id: str | None = Field(default=None, description="契约切片 ID")
    seq: int = Field(..., description="切片序号")
    heading: str | None = Field(default=None, description="所属标题路径")
    content: str = Field(..., description="切片正文")
    page_from: int | None = Field(default=None, description="起始页")
    page_to: int | None = Field(default=None, description="结束页")
    source_type: str | None = Field(default=None, description="切片来源类型")
    content_hash: str = Field(..., description="切片指纹")
    status: str = Field(..., description="online/archived")


class KbPublicationChunkDetailOutSchema(KbPublicationChunkOutSchema):
    """切片详情响应（管理端只读查看，不返回向量）。"""


class KbPublicationBaselineChunkOutSchema(BaseModel):
    """03.2 已批准基线中的一个正式切片（`chunk_id` 必返）。"""

    chunk_id: str = Field(..., description="AI 分配的正式切片 ID")
    seq: int = Field(..., description="切片序号")
    heading: str | None = Field(default=None, description="切片小标题")
    content: str = Field(..., description="切片正文")
    page_from: int | None = Field(default=None, description="起始页")
    page_to: int | None = Field(default=None, description="结束页")


class KbPublicationBaselineOutSchema(BaseModel):
    """03.2 读取知识修订基线响应（管理端只读）。"""

    publication_id: str = Field(..., description="发布单元 ID")
    resource_type: str = Field(..., description="document / faq")
    resource_id: str = Field(..., description="GSI 知识主记录 ID")
    record_id: str = Field(..., description="已批准快照 ID")
    version_id: str = Field(..., description="契约版本 ID")
    revision: int = Field(..., description="索引修订号")
    knowledge_no: str = Field(..., description="知识编号")
    metadata: dict[str, Any] = Field(default_factory=dict, description="契约元数据快照")
    content_hash: str = Field(..., description="整包内容摘要")
    chunks: list[KbPublicationBaselineChunkOutSchema] = Field(default_factory=list, description="本页切片")
    searchable: bool = Field(default=False, description="本地索引是否就绪")
    next_cursor: str | None = Field(default=None, description="下一页游标，结束为 null")


class KbPublicationSnapshotDeleteSchema(BaseModel):
    """03.4 删除已批准切片集合（维护页单个快照删除）。"""

    record_id: str = Field(..., min_length=1, max_length=64, description="要删除的快照 ID")
    reason: str = Field(..., min_length=1, max_length=200, description="删除原因")

    @field_validator("record_id", "reason")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        return ensure_non_blank(value)


class KbPublicationSnapshotDeleteOutSchema(BaseModel):
    """03.4 删除回执。"""

    publication_id: str = Field(..., description="发布单元 ID")
    record_id: str = Field(..., description="被删除的快照 ID")
    revision: int = Field(..., description="删除事件的修订号")
    deleted: bool = Field(default=True, description="是否删除成功")
    searchable: bool = Field(default=False, description="删除后不可检索")


class KbPublicationEntryWriteSchema(BaseModel):
    """管理端写入正文（03.1 首发 / 空壳补录）：切片由服务层按 ``PARSE_CHUNK_SIZE`` 切分。"""

    publication_id: str | None = Field(
        default=None, min_length=1, max_length=64, description="发布单元 ID，省略则自动生成"
    )
    resource_type: Literal["document", "faq"] = Field(default="document", description="知识类型")
    tenant_id: str | None = Field(default=None, max_length=64, description="租户 ID")
    resource_id: str | None = Field(default=None, max_length=64, description="原始资源 ID")
    title: str = Field(..., min_length=1, max_length=200, description="标题")
    category: str | None = Field(default=None, max_length=64, description="知识分类")
    summary: str | None = Field(default=None, description="内容摘要")
    tags: list[str] = Field(default_factory=list, description="标签")
    owner: str | None = Field(default=None, max_length=64, description="维护人")
    knowledge_no: str | None = Field(default=None, max_length=64, description="知识编号，省略取发布单元 ID")
    content: str = Field(..., description="正文（Markdown/纯文本），服务端自动切分并向量化")

    @field_validator("publication_id", "title", "content")
    @classmethod
    def validate_required_text(cls, value: str | None) -> str | None:
        return ensure_non_blank(value) if value is not None else None

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, value: list[str]) -> list[str]:
        return _normalize_tags(value)


class KbPublicationEntryReviseSchema(BaseModel):
    """管理端修订正文（03.3）：整篇重新切分写入新快照，旧快照保留。"""

    content: str = Field(..., description="修订后的正文（整篇），服务端自动切分并向量化")
    base_record_id: str | None = Field(
        default=None, min_length=1, max_length=64, description="进入修订时的快照 ID，省略取当前生效快照"
    )
    title: str | None = Field(default=None, min_length=1, max_length=200, description="标题")
    category: str | None = Field(default=None, max_length=64, description="知识分类")
    summary: str | None = Field(default=None, description="内容摘要")
    tags: list[str] | None = Field(default=None, description="标签")
    owner: str | None = Field(default=None, max_length=64, description="维护人")

    @field_validator("content", "base_record_id")
    @classmethod
    def validate_required_text(cls, value: str | None) -> str | None:
        return ensure_non_blank(value) if value is not None else None

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str | None) -> str | None:
        return ensure_non_blank(value) if value is not None else None

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, value: list[str] | None) -> list[str] | None:
        return _normalize_tags(value) if value is not None else None


class KbPublicationDeactivateSchema(BaseModel):
    """#20 停用已生效知识（D05 同口径）：切片归档、停止召回，正文保留供审计。"""

    reason: str = Field(..., min_length=1, max_length=200, description="停用原因（必填，落审计日志）")

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        return ensure_non_blank(value)


class KbPublicationEntryWriteOutSchema(BaseModel):
    """正文写入回执：切片数、摘要与落库版本，前端据此刷新详情。"""

    publication_id: str = Field(..., description="发布单元 ID")
    record_id: str = Field(..., description="新快照 ID")
    version_id: str = Field(..., description="契约版本 ID")
    revision: int = Field(..., description="新修订号")
    content_hash: str = Field(..., description="整包内容摘要（sha256:<hex>）")
    chunk_count: int = Field(..., description="本次写入的切片数")
    searchable: bool = Field(..., description="写入后是否可检索")
    status: str = Field(..., description="写入后的发布单元状态")


class KbPublicationDeactivateOutSchema(BaseModel):
    """#20 停用回执：固定 ``deactivated=true`` / ``searchable=false``。"""

    publication_id: str = Field(..., description="发布单元 ID")
    record_id: str = Field(..., description="被停用的快照 ID")
    revision: int = Field(..., description="停用事件的修订号")
    deactivated: bool = Field(default=True, description="是否停用成功")
    searchable: bool = Field(default=False, description="停用后不可检索")
    status: str = Field(..., description="停用后的发布单元状态")
