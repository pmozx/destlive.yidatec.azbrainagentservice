"""重复分组治理 Schema（#16–#20 物化结果的只读视图）。"""

from typing import Any

from pydantic import BaseModel, Field

from app.core.base_schema import BaseQueryParam, BaseSchema


class KbDupGroupOutSchema(BaseSchema):
    """重复分组列表响应（成员详情放详情接口，列表只回条数）。"""

    tenant_id: str = Field(default="", description="租户 ID")
    group_key: str = Field(default="", description="契约 group_id（类型 + 成员身份的稳定摘要）")
    kind: str = Field(default="", description="doc_exact/doc_similar/chunk/faq_ai/faq_manual")
    title: str = Field(default="", description="分组标题（建议主项标题）")
    score_method: str = Field(default="", description="得分方法版本")
    suggested_keep_id: str | None = Field(default=None, description="建议保留项 hit_id")
    result_id: str | None = Field(default=None, description="最近一次产出该分组的检测结果 ID")
    resource_type: str = Field(..., description="资源类型")
    status: str = Field(..., description="active/merged/dismissed")
    similarity: float | None = Field(default=None, description="组内最低相似度")
    member_count: int = Field(default=0, description="组内条目数")


class KbDupGroupDetailOutSchema(KbDupGroupOutSchema):
    """重复分组详情响应（含检测范围、成员与合并预览）。"""

    reason: str = Field(default="", description="判断依据")
    scope: dict[str, Any] | None = Field(default=None, description="检测范围")
    members: list[dict[str, Any]] = Field(default_factory=list, description="组内条目")
    merged_preview: dict[str, Any] | None = Field(default=None, description="合并预览（草稿态）")


class KbDupGroupQueryParam(BaseQueryParam):
    """重复分组查询参数。"""

    tenant_id: str | None = Field(None, description="租户 ID", json_schema_extra={"q": "eq"})
    kind: str | None = Field(None, description="治理分组类型", json_schema_extra={"q": "eq"})
    resource_type: str | None = Field(None, description="资源类型", json_schema_extra={"q": "eq"})
    status: str | None = Field(None, description="治理状态", json_schema_extra={"q": "eq"})


class KbDupGroupStatusSchema(BaseModel):
    """处理重复分组：确认合并或忽略。"""

    status: str = Field(..., pattern="^(merged|dismissed)$", description="merged/dismissed")
