"""GSI FAQ 审批快照出入参 Schema。"""

from pydantic import BaseModel, ConfigDict, Field


class SbpFaqRecordOutSchema(BaseModel):
    """FAQ 审批快照列表输出（不含大字段 snapshot_json）。"""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="记录主键")
    faq_id: str = Field(..., description="FAQ 主键")
    stage: str = Field(
        ..., description="阶段：version保存内容 owner内容审 audience受众审 owner_final终审发布"
    )
    status: str = Field(
        ..., description="状态：saved/pending/approved/returned/cancelled"
    )


class SbpFaqRecordDetailOutSchema(SbpFaqRecordOutSchema):
    """FAQ 审批快照详情输出（含 snapshot_json）。"""

    snapshot_json: str | None = Field(default=None, description="审批快照 JSON 原文")


class SbpFaqRecordQueryParam(BaseModel):
    """FAQ 审批快照查询参数。"""

    faq_id: str | None = Field(default=None, description="FAQ 主键（精确）")
    stage: str | None = Field(default=None, description="阶段（精确）")
    status: str | None = Field(default=None, description="状态（精确）")
