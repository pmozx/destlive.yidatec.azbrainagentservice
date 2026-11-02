"""GSI 知识生效快照出入参 Schema。"""

from pydantic import BaseModel, ConfigDict, Field


class SbpKmDocRecordOutSchema(BaseModel):
    """知识生效快照列表输出（不含大字段 snapshot_json）。"""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="记录主键")
    doc_id: str = Field(..., description="知识主键")
    stage: str = Field(..., description="记录阶段：save/owner/audience")
    status: str = Field(
        ..., description="状态：saved/pending/approved/returned/cancelled"
    )


class SbpKmDocRecordDetailOutSchema(SbpKmDocRecordOutSchema):
    """知识生效快照详情输出（含 snapshot_json）。"""

    snapshot_json: str = Field(..., description="整篇知识不可变快照")


class SbpKmDocRecordQueryParam(BaseModel):
    """知识生效快照查询参数。"""

    doc_id: str | None = Field(
        default=None, description="知识主键", json_schema_extra={"q": "eq"}
    )
    stage: str | None = Field(
        default=None, description="记录阶段", json_schema_extra={"q": "eq"}
    )
    status: str | None = Field(
        default=None, description="状态", json_schema_extra={"q": "eq"}
    )
