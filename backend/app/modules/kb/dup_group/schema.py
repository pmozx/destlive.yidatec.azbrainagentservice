"""重复分组治理 Schema（#16–#20 物化结果的只读视图 + 管理端主动检测/查重/预览入参）。

只读部分是契约物化结果的展示投影；入参部分是「管理端没有 GSI 签发 scope」的替代：
范围改由 ``publication_ids`` 表达，由 ``dup_result.scope`` 反查生成正式 scope。
"""

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.core.base_schema import BaseQueryParam, BaseSchema
from app.core.validator import DateTimeStr

from ..dup_result.schema import DUP_KINDS, SCORE_PROFILE


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


# ── 管理端主动治理（检测 / 保存前查重 / 合并预览） ─────────────────────


class KbDupScopeSchema(BaseModel):
    """治理范围：留空表示全部生效发布单元。

    管理端不签发契约 ``scope``，这里只表达「比谁」，真正的 ``resources``
    （``record_id`` / ``content_hash``）由后端按当前生效快照反查生成。
    """

    tenant_id: str | None = Field(default=None, max_length=64, description="租户 ID；留空按范围推导")
    publication_ids: list[str] = Field(
        default_factory=list, description="参与治理的发布单元；留空表示全部生效发布单元"
    )
    kinds: list[str] = Field(default_factory=lambda: list(DUP_KINDS), description="治理类型（五类）")
    score_profile: str = Field(default=SCORE_PROFILE, max_length=64, description="策略版本")


class KbDupDetectSchema(KbDupScopeSchema):
    """触发一次重复检测：落 30 分钟快照并物化治理分组。"""

    limit: int = Field(default=100, ge=1, le=100, description="本次返回的分组条数上限")


class KbDupDetectOutSchema(BaseModel):
    """检测结果：只回本次产出的快照与分组，翻页请用 ``groups/query`` 口径的 result_id。"""

    result_id: str = Field(..., description="检测结果快照 ID（30 分钟内可用于合并预览）")
    expires_at: DateTimeStr = Field(..., description="快照过期时间")
    score_profile: str = Field(..., description="策略版本")
    scope_size: int = Field(..., ge=0, description="本次参与比较的内容条数")
    total_groups: int = Field(..., ge=0, description="本次产出的分组总数")
    returned_groups: int = Field(..., ge=0, description="本响应返回的分组条数")
    coverage: dict[str, Any] = Field(default_factory=dict, description="覆盖度")
    items: list[dict[str, Any]] = Field(default_factory=list, description="分组列表（契约分组结构）")


class KbDupCandidateChunkSchema(BaseModel):
    """候选内容的一个切片（``chunk_id`` 留空表示尚未入库）。"""

    seq: int = Field(..., ge=1, description="切片序号")
    content: str = Field(..., min_length=1, description="切片正文")
    chunk_id: str | None = Field(default=None, max_length=64, description="已入库切片的 chunk_id")
    title: str | None = Field(default=None, max_length=200, description="切片小标题")


class KbDupCandidateSchema(BaseModel):
    """待保存的候选内容：只在本次比较里使用，不落任何正式表。"""

    resource_type: Literal["document", "faq"] = Field(..., description="资源类型")
    resource_id: str = Field(..., min_length=1, max_length=64, description="业务资源 ID")
    record_id: str = Field(default="", max_length=64, description="内容记录 ID（草稿可留空）")
    content_hash: str = Field(default="", max_length=80, description="内容摘要（草稿可留空）")
    title: str = Field(..., min_length=1, max_length=200, description="标题")
    summary: str | None = Field(default=None, description="摘要")
    content: str = Field(default="", description="整篇正文（不给切片时按整篇比较）")
    chunks: list[KbDupCandidateChunkSchema] = Field(default_factory=list, description="切片列表")


class KbDupCheckSchema(KbDupScopeSchema):
    """保存前查重：候选内容 vs 治理范围。"""

    candidate: KbDupCandidateSchema = Field(..., description="待保存内容")


class KbDupCheckOutSchema(BaseModel):
    """查重结果：只报命中，不阻止保存（是否保存由调用方决定）。"""

    request_id: str = Field(..., description="本次查重请求 ID")
    is_duplicate: bool = Field(..., description="是否命中重复")
    similarity: float = Field(..., ge=0, le=1, description="最高相似度")
    score_profile: str = Field(..., description="策略版本")
    scope_size: int = Field(..., ge=0, description="本次参与比较的内容条数")
    matches: list[dict[str, Any]] = Field(default_factory=list, description="命中列表")


class KbDupPreviewActionSchema(BaseModel):
    """单个成员的处置动作。"""

    target_hit_id: str = Field(..., min_length=1, max_length=64, description="被处置成员 hit_id")
    action: Literal["keep", "drop", "discard", "merge_answer"] = Field(..., description="处置动作")
    merged_answer_html: str | None = Field(default=None, description="merge_answer 时的答案正文")


class KbDupPreviewSchema(BaseModel):
    """生成合并预览：范围与 result_id 取自分组行，调用方只给人工决策。"""

    keep_hit_id: str | None = Field(default=None, max_length=64, description="保留项 hit_id；留空用建议主项")
    actions: list[KbDupPreviewActionSchema] = Field(default_factory=list, description="处置动作列表")
    prompt_text: str = Field(default="", max_length=4000, description="合并提示词")
    model_key: str = Field(default="default", max_length=64, description="模型键")


class KbDupPreviewOutSchema(BaseModel):
    """合并预览结果：草稿态，不落正式库。"""

    preview_id: str = Field(..., description="预览 ID")
    preview_hash: str = Field(..., description="预览内容摘要")
    title: str = Field(..., description="合并后标题")
    answer_html: str = Field(default="", description="合并后答案正文（非 FAQ 组为空）")
    summary: str = Field(default="", description="合并后摘要")
    source_hit_ids: list[str] = Field(default_factory=list, description="参与合并的成员")
    conflicts: list[dict[str, Any]] = Field(default_factory=list, description="未解决的事实矛盾")
    warnings: list[str] = Field(default_factory=list, description="告警")

