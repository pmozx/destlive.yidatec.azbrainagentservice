"""GSI 受众团队成员出入参 Schema。"""

from pydantic import BaseModel, ConfigDict, Field


class SbpSysAudienceTeamMemberOutSchema(BaseModel):
    """受众团队成员输出。"""

    model_config = ConfigDict(from_attributes=True)

    team_id: int = Field(..., description="受众团队ID")
    user_id: int = Field(..., description="用户ID")


class SbpSysAudienceTeamMemberQueryParam(BaseModel):
    """受众团队成员查询参数。"""

    team_id: int | None = Field(
        default=None, description="受众团队ID", json_schema_extra={"q": "eq"}
    )
    user_id: int | None = Field(
        default=None, description="用户ID", json_schema_extra={"q": "eq"}
    )
