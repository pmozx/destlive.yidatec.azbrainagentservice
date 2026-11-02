"""GSI 知识文档出入参 Schema。"""

from datetime import date

from pydantic import BaseModel, ConfigDict, Field


class SbpKmDocOutSchema(BaseModel):
    """知识文档元数据输出。"""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="知识主键")
    title: str = Field(..., description="知识标题")
    status: str = Field(..., description="业务状态：Draft/Published/Unpublished 等")
    doc_type: str = Field(..., description="知识类型：Document/FAQ/SOP/API/Handover 等")
    domain: str | None = Field(default=None, description="业务域/分类")
    team: str | None = Field(default=None, description="归属业务团队")
    expire_at: date | None = Field(default=None, description="有效期截止日期")
    knowledge_no: str | None = Field(default=None, description="知识编号")
    deleted: int = Field(..., description="逻辑删除：0否 1是")


class SbpKmDocQueryParam(BaseModel):
    """知识文档查询参数。"""

    title: str | None = Field(
        default=None, description="知识标题", json_schema_extra={"q": "like"}
    )
    status: str | None = Field(
        default=None, description="业务状态", json_schema_extra={"q": "eq"}
    )
    doc_type: str | None = Field(
        default=None, description="知识类型", json_schema_extra={"q": "eq"}
    )
    domain: str | None = Field(
        default=None, description="业务域/分类", json_schema_extra={"q": "eq"}
    )
    team: str | None = Field(
        default=None, description="归属业务团队", json_schema_extra={"q": "eq"}
    )
    knowledge_no: str | None = Field(
        default=None, description="知识编号", json_schema_extra={"q": "eq"}
    )
    deleted: int | None = Field(
        default=None, description="逻辑删除：0否 1是", json_schema_extra={"q": "eq"}
    )
