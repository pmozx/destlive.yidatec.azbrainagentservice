"""GSI FAQ 按团队生效关系出入参 Schema。

FAQ 侧的授权规则是「快照化」的：``publication.access_rules_json`` 优先，
为空才回落到 ``record.snapshot_json``；两者都必须含 ``invisibleTeams`` 数组，
缺失或 JSON 非法即视为该发布单元不可用（不能拿编辑态受众表补权限）。
"""

from pydantic import BaseModel, ConfigDict, Field


class SbpFaqPublicationOutSchema(BaseModel):
    """FAQ 生效关系 + 生效规则解析结果。"""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="生效记录主键")
    faq_id: str = Field(..., description="FAQ 主键")
    team: str = Field(..., description="获授权受众团队（存储值为 group_code）")
    record_id: str = Field(..., description="当前生效的 FAQ 审批快照")
    index_status: str = Field(..., description="索引同步状态：pending/succeeded/failed")
    updated_at: str | None = Field(default=None, description="更新时间（业务本地时间）")
    access_rules_json: str | None = Field(
        default=None, description="publication 上的生效规则原文；为空时回落 record 快照"
    )
    rules_source: str = Field(
        default="none", description="生效规则来源：publication / record / none"
    )
    rules_valid: bool = Field(
        default=False, description="规则有效：能解析且含 invisibleTeams 数组"
    )
    invisible_teams: list[str] = Field(
        default_factory=list, description="不可见团队（黑名单优先，命中即拒绝）"
    )
    rules_error: str | None = Field(default=None, description="规则无效原因")


class SbpFaqPublicationQueryParam(BaseModel):
    """FAQ 生效关系查询参数。"""

    faq_id: str | None = Field(default=None, description="FAQ 主键（精确）")
    team: str | None = Field(default=None, description="团队编码（精确）")
    record_id: str | None = Field(default=None, description="审批快照主键（精确）")
    index_status: str | None = Field(default=None, description="索引同步状态")
