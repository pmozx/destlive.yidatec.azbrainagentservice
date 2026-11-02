"""知识维护（管理端）Schema：写入口只做字段级校验，业务规则放在 Service 层。"""

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.core.base_schema import BaseQueryParam, BaseSchema
from app.core.validator import DateTimeStr, ensure_non_blank

# 知识类型与发布单元状态的白名单（与模型 CheckConstraint 保持同一口径）
RESOURCE_TYPES: tuple[str, ...] = ("document", "faq")
PUBLICATION_STATUSES: tuple[str, ...] = ("active", "inactive", "deleted")


def _normalize_tags(tags: list[str]) -> list[str]:
    """标签归一：去首尾空白、去空值、保序去重，避免前端重复标签落库。"""
    result: list[str] = []
    for tag in tags:
        text = tag.strip()
        if text and text not in result:
            result.append(text)
    return result


class KbKnowledgeCreateSchema(BaseModel):
    """新增知识条目：同时创建发布单元与管理端元数据。"""

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


class KbKnowledgeUpdateSchema(BaseModel):
    """编辑知识条目：只更新调用方实际传入的字段（显式传 null 表示清空可空字段）。"""

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


class KbKnowledgeStatusSchema(BaseModel):
    """上下线：只改发布单元状态，不动内容与版本。"""

    status: Literal["active", "inactive", "deleted"] = Field(..., description="active/inactive/deleted")


class KbKnowledgeOutSchema(BaseSchema):
    """知识条目列表响应（元数据 + 发布单元指针摘要）。"""

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


class KbKnowledgeDetailOutSchema(KbKnowledgeOutSchema):
    """知识条目详情响应（补充版本数/切片数，便于详情页首屏展示）。"""

    version_count: int = Field(default=0, description="版本快照数")
    chunk_count: int = Field(default=0, description="切片总数")


class KbKnowledgeQueryParam(BaseQueryParam):
    """知识条目查询参数（``q`` 标记决定 CRUD 层的比较方式）。"""

    publication_id: str | None = Field(None, description="发布单元 ID", json_schema_extra={"q": "like"})
    title: str | None = Field(None, description="标题", json_schema_extra={"q": "like"})
    category: str | None = Field(None, description="知识分类", json_schema_extra={"q": "eq"})
    owner: str | None = Field(None, description="维护人", json_schema_extra={"q": "like"})
    resource_type: str | None = Field(None, description="知识类型", json_schema_extra={"q": "eq"})
    status: str | None = Field(None, description="发布单元状态", json_schema_extra={"q": "eq"})


class KbKnowledgeRevisionOutSchema(BaseSchema):
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


class KbKnowledgeRevisionDetailOutSchema(KbKnowledgeRevisionOutSchema):
    """版本快照详情响应（含契约透传元数据）。"""

    # 模型属性名是 metadata_（metadata 被 Declarative 占用），对外仍按契约字段名输出
    metadata: dict[str, Any] | None = Field(
        default=None,
        validation_alias="metadata_",
        serialization_alias="metadata",
        description="契约透传元数据",
    )


class KbKnowledgeChunkOutSchema(BaseSchema):
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


class KbKnowledgeChunkDetailOutSchema(KbKnowledgeChunkOutSchema):
    """切片详情响应（管理端只读查看，不返回向量）。"""
