"""GSI 知识生效快照出入参 Schema。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules_gsi.base import format_gsi_datetime


class SbpKmDocRecordOutSchema(BaseModel):
    """知识生效快照列表输出（不含大字段 snapshot_json）。"""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="记录主键")
    doc_id: str = Field(..., description="知识主键")
    stage: str = Field(..., description="记录阶段：save/owner/audience")
    team: str | None = Field(default=None, description="受众团队编码，内容审为空")
    assignee: str | None = Field(default=None, description="审批人账号")
    created_by: str | None = Field(default=None, description="送审人账号")
    status: str = Field(
        ..., description="状态：saved/pending/approved/returned/cancelled"
    )
    created_at: str | None = Field(default=None, description="送审时间（业务本地时间）")
    updated_at: str | None = Field(default=None, description="更新时间（业务本地时间）")

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def _format_gsi_time(cls, value: datetime | str | None) -> str | None:
        """GSI 时间列落库即业务本地时间（表结构 §1.9），原样格式化、不做时区换算。

        放在字段校验里而不是 service 循环：列表走通用 ``SbpReadCRUD.page``、详情走
        ``get_or_404``，两条路径都只做一次 ``model_validate``，在这里统一最省。
        """
        return format_gsi_datetime(value) if isinstance(value, datetime) else value


class SbpKmDocRecordDetailOutSchema(SbpKmDocRecordOutSchema):
    """知识生效快照详情输出（含 snapshot_json 与审批意见）。"""

    snapshot_json: str = Field(..., description="整篇知识不可变快照")
    comment: str | None = Field(default=None, description="保存说明或审批意见")


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
