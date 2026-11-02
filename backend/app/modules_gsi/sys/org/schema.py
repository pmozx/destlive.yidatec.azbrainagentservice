"""GSI 组织架构出入参 Schema。"""

from pydantic import BaseModel, ConfigDict, Field


class SbpSysOrgOutSchema(BaseModel):
    """组织架构输出。"""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="组织ID")
    parent_id: int = Field(..., description="上级组织ID，0为顶级")
    name: str = Field(..., description="组织名称")
    code: str = Field(..., description="组织编码")
    type: str = Field(..., description="节点类型")
    category: str = Field(..., description="组织分类：admin行政 business业务")
    source_org_id: int | None = Field(
        default=None, description="业务镜像对应的行政组织ID"
    )
    status: str = Field(..., description="状态：1启用 0停用")
    deleted: int = Field(..., description="逻辑删除：0否 1是")


class SbpSysOrgQueryParam(BaseModel):
    """组织架构查询参数。"""

    parent_id: int | None = Field(
        default=None, description="上级组织ID", json_schema_extra={"q": "eq"}
    )
    name: str | None = Field(
        default=None, description="组织名称", json_schema_extra={"q": "like"}
    )
    code: str | None = Field(
        default=None, description="组织编码", json_schema_extra={"q": "like"}
    )
    type: str | None = Field(
        default=None, description="节点类型", json_schema_extra={"q": "eq"}
    )
    category: str | None = Field(
        default=None, description="组织分类", json_schema_extra={"q": "eq"}
    )
    source_org_id: int | None = Field(
        default=None, description="行政组织ID", json_schema_extra={"q": "eq"}
    )
    status: str | None = Field(
        default=None, description="状态：1启用 0停用", json_schema_extra={"q": "eq"}
    )
    deleted: int | None = Field(
        default=None, description="逻辑删除：0否 1是", json_schema_extra={"q": "eq"}
    )
