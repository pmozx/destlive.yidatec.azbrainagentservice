"""GSI 文档按团队生效关系出入参 Schema。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SbpKmDocPublicationOutSchema(BaseModel):
    """知识按受众团队生效内容输出。"""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="生效记录主键")
    doc_id: str = Field(..., description="知识主键")
    team: str = Field(..., description="获授权受众团队")
    record_id: str = Field(..., description="当前生效的整篇送审快照")
    index_status: str = Field(..., description="索引同步状态：pending/succeeded/failed")
    updated_at: datetime = Field(..., description="生效更新时间")


class SbpKmDocPublicationQueryParam(BaseModel):
    """文档按团队生效关系查询参数。"""

    doc_id: str | None = Field(
        default=None, description="知识主键", json_schema_extra={"q": "eq"}
    )
    team: str | None = Field(
        default=None, description="获授权受众团队", json_schema_extra={"q": "eq"}
    )
    record_id: str | None = Field(
        default=None, description="生效快照主键", json_schema_extra={"q": "eq"}
    )
    index_status: str | None = Field(
        default=None, description="索引同步状态", json_schema_extra={"q": "eq"}
    )
