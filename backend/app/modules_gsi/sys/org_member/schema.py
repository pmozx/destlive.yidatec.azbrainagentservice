"""GSI 组织成员出入参 Schema。"""

from pydantic import BaseModel, ConfigDict, Field


class SbpSysOrgMemberOutSchema(BaseModel):
    """组织成员输出。"""

    model_config = ConfigDict(from_attributes=True)

    org_id: int = Field(..., description="组织ID")
    user_id: int = Field(..., description="用户ID")


class SbpSysOrgMemberQueryParam(BaseModel):
    """组织成员查询参数。"""

    org_id: int | None = Field(
        default=None, description="组织ID", json_schema_extra={"q": "eq"}
    )
    user_id: int | None = Field(
        default=None, description="用户ID", json_schema_extra={"q": "eq"}
    )
