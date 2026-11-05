"""GSI 受众团队成员出入参 Schema。"""

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class SbpSysAudienceTeamMemberWithTeamOutSchema(BaseModel):
    """受众团队成员 + 团队三标识 + 用户有效性。

    关联表本身只有 ``team_id`` / ``user_id`` 两个数字；本模型把团队编码、名称、
    兼容标识与用户状态一并给出，页面才能直观判断「谁在哪个团队、该团队是否有效」。
    有效性在模型内派生，避免 service / 前端各算一遍。
    """

    model_config = ConfigDict(from_attributes=True)

    team_id: int = Field(..., description="受众团队ID")
    group_code: str | None = Field(default=None, description="团队编码：授权判定唯一依据")
    group_name: str | None = Field(default=None, description="团队名称")
    audience_team: str | None = Field(default=None, description="受众团队兼容标识")
    team_status: str | None = Field(default=None, description="团队状态：1启用 0停用")
    team_deleted: int | None = Field(default=None, description="团队逻辑删除：0否 1是")
    team_valid: bool = Field(default=False, description="团队有效：status=1 且 deleted=0")
    user_id: int = Field(..., description="用户ID")
    user_exists: bool = Field(default=False, description="用户是否存在于 sbp_sys_user")
    user_status: str | None = Field(default=None, description="在职状态：1在职 0离职")
    allow_login: str | None = Field(default=None, description="是否允许登录：1允许 0禁止")
    user_deleted: int | None = Field(default=None, description="用户逻辑删除：0否 1是")
    user_valid: bool = Field(default=False, description="用户有效：存在、在职且未删除")

    @model_validator(mode="after")
    def _derive_valid_flags(self) -> "SbpSysAudienceTeamMemberWithTeamOutSchema":
        """派生两个有效性标记：判定口径只写在这里。"""
        self.team_valid = self.team_status == "1" and self.team_deleted == 0
        self.user_exists = self.user_status is not None
        self.user_valid = (
            self.user_exists and self.user_status == "1" and self.user_deleted == 0
        )
        return self


class SbpSysAudienceTeamMemberWithTeamQueryParam(BaseModel):
    """受众团队成员（含团队标识）查询参数；三个标识都可单独筛选。"""

    team_id: int | None = Field(default=None, description="受众团队ID")
    user_id: int | None = Field(default=None, description="用户ID")
    group_code: str | None = Field(default=None, description="团队编码（精确）")
    group_name: str | None = Field(default=None, description="团队名称（模糊）")
    audience_team: str | None = Field(default=None, description="受众团队兼容标识（精确）")
    team_status: str | None = Field(default=None, description="团队状态：1启用 0停用")
    user_status: str | None = Field(default=None, description="用户在职状态：1在职 0离职")
    valid_only: bool = Field(
        default=False, description="仅看有效团队（status=1 且 deleted=0）"
    )


class SbpUserTeamOutSchema(BaseModel):
    """单个团队标识（按用户反查结果项）。"""

    team_id: int = Field(..., description="受众团队ID")
    group_code: str = Field(..., description="团队编码：授权判定唯一依据")
    group_name: str = Field(..., description="团队名称")
    audience_team: str | None = Field(default=None, description="受众团队兼容标识")
    team_status: str = Field(..., description="团队状态：1启用 0停用")
    team_deleted: int = Field(..., description="团队逻辑删除：0否 1是")
    team_valid: bool = Field(default=False, description="团队有效：status=1 且 deleted=0")

    @model_validator(mode="after")
    def _derive_valid_flag(self) -> "SbpUserTeamOutSchema":
        self.team_valid = self.team_status == "1" and self.team_deleted == 0
        return self


class SbpUserTeamsOutSchema(BaseModel):
    """按用户反查的团队标识集合与用户有效性。

    ``teams`` 为空即该用户没有任何受众团队，检索侧就是「无知识授权」，
    而不是「全库可见」；``valid_team_count`` 区分「有团队但都被停用/删除」。
    """

    user_id: int = Field(..., description="用户ID")
    user_exists: bool = Field(default=False, description="用户是否存在于 sbp_sys_user")
    user_status: str | None = Field(default=None, description="在职状态：1在职 0离职")
    allow_login: str | None = Field(default=None, description="是否允许登录：1允许 0禁止")
    user_deleted: int | None = Field(default=None, description="用户逻辑删除：0否 1是")
    team_count: int = Field(default=0, description="团队总数（含停用/已删除）")
    valid_team_count: int = Field(default=0, description="有效团队数")
    teams: list[SbpUserTeamOutSchema] = Field(default_factory=list, description="团队标识列表")
