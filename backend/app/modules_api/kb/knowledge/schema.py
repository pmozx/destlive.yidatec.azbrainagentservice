"""知识写入 / 基线读取 / 检索引擎 Schema（03.1 新增 / 03.2 读取基线 / 03.3 修改 / 03.4 删除 / #07 检索）。

``content_hash`` 的规则尚未与 GSI 对齐（Q9），``canonical_content_hash`` 即本侧口径；
对齐后如有变化，只改这一处并同步 ``app.scripts.contract.mock_gsi`` 的样例构造。

注意：v3 起 ``chunks[].chunk_id`` 变为可选（新增切片由 AI 分配），因此摘要只对
调用方双方都能确定的字段计算，**不含 chunk_id**，否则 GSI 无法在提交前算出同一个摘要。
"""

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

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


class EntryDeactivateRequest(BaseModel):
    """#20 停用已生效知识请求正文（D05：通用接口，不带 resource_type / resource_id）。"""

    model_config = ConfigDict(extra="ignore")

    request_id: str
    tenant_id: str
    user_id: str
    publication_id: str
    record_id: str
    revision: int = Field(ge=1, description="停用后的新修订号，必须大于当前值")
    reason: str

    @field_validator("request_id", "tenant_id", "user_id", "publication_id", "record_id", "reason")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return ensure_non_blank(value)


class EntryDeactivateReceipt(BaseModel):
    """#20 停用回执：`deactivated` / `searchable` 是固定值，回显给调用方免除二次猜测。"""

    request_id: str
    publication_id: str
    record_id: str
    revision: int
    deactivated: bool
    searchable: bool


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


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# #07 关键词 / 自然语言检索
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class SearchAuthorization(BaseModel):
    """#07 的短期授权块。

    跨度 ≤120s 与过期拒绝由路由依赖 ``enforced_authorization`` 统一校验；
    这里只声明必填，避免「缺块被当成可选」——契约要求 #07 必须携带。
    """

    model_config = ConfigDict(extra="ignore")

    scope_id: str = Field(description="GSI 本次授权追踪标识")
    issued_at: int = Field(ge=0, description="签发时间，Unix 毫秒")
    expires_at: int = Field(ge=0, description="到期时间，Unix 毫秒")

    @field_validator("scope_id")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return ensure_non_blank(value)


class SearchRequest(BaseModel):
    """#07 请求正文（字段与手册 §七「04 关键词 / 自然语言检索」一致）。"""

    model_config = ConfigDict(extra="ignore")

    request_id: str
    query: str = Field(description="查询词或自然语言问题，非空")
    mode: Literal["keyword", "natural_language"] = Field(description="keyword=关键词；natural_language=自然语言")
    limit: int = Field(ge=1, le=100, description="返回条数上限，1–100")
    tenant_id: str
    user_id: str = Field(description="GSI 登录用户的稳定数字主键（字符串）")
    authorization: SearchAuthorization

    @field_validator("request_id", "query", "tenant_id", "user_id")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return ensure_non_blank(value)


class SearchHitItem(BaseModel):
    """#07 单条命中（chunk 级）：``score`` 是模式内相对分 0–1，不是回答正确率。"""

    resource_type: str
    resource_id: str
    publication_id: str
    record_id: str
    version_id: str
    revision: int
    knowledge_no: str
    chunk_id: str
    title: str
    snippet: str
    page_from: int | None = None
    page_to: int | None = None
    score: float


class SearchResponse(BaseModel):
    """#07 响应：``returned_count`` 必须等于 ``hits.length``，无命中时 ``no_evidence=true``。"""

    request_id: str
    hits: list[SearchHitItem]
    returned_count: int
    no_evidence: bool

