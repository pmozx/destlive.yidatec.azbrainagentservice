"""GSI 受众团队出入参 Schema。"""

from pydantic import BaseModel, ConfigDict, Field


class SbpSysAudienceTeamOutSchema(BaseModel):
    """受众团队输出。"""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="受众团队ID")
    group_code: str = Field(..., description="团队编码")
    group_name: str = Field(..., description="团队名称")
    audience_team: str | None = Field(default=None, description="受众团队标识")
    status: str = Field(..., description="状态：1启用 0停用")
    deleted: int = Field(..., description="逻辑删除：0否 1是")


class SbpSysAudienceTeamQueryParam(BaseModel):
    """受众团队查询参数。"""

    group_code: str | None = Field(
        default=None, description="团队编码", json_schema_extra={"q": "eq"}
    )
    group_name: str | None = Field(
        default=None, description="团队名称", json_schema_extra={"q": "like"}
    )
    audience_team: str | None = Field(
        default=None, description="受众团队标识", json_schema_extra={"q": "eq"}
    )
    status: str | None = Field(
        default=None, description="状态：1启用 0停用", json_schema_extra={"q": "eq"}
    )
    deleted: int | None = Field(
        default=None, description="逻辑删除：0否 1是", json_schema_extra={"q": "eq"}
    )
