"""GSI 用户档案出入参 Schema。"""

from pydantic import BaseModel, ConfigDict, Field


class SbpSysUserOutSchema(BaseModel):
    """用户档案输出（不含密码哈希、邮箱、手机号）。

    ``rank_name`` 由联表 ``sbp_sys_rank`` 带出；只读单表时可能为空。
    """

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="用户ID")
    username: str = Field(..., description="登录账号（K 账号）")
    cn_name: str = Field(default="", description="中文名")
    en_name: str = Field(default="", description="英文名")
    rank_id: int | None = Field(default=None, description="职级ID")
    rank_name: str | None = Field(default=None, description="职级名称（联表带出）")
    position_name: str = Field(default="", description="职位名称（快照）")
    status: str = Field(..., description="在职状态：1在职 0离职")
    allow_login: str = Field(..., description="是否允许登录：1允许 0禁止")
    deleted: int = Field(..., description="逻辑删除：0否 1是")
    org_count: int | None = Field(default=None, description="组织归属数（列表联表统计）")
    team_count: int | None = Field(default=None, description="受众团队归属数（列表联表统计）")


class SbpSysUserQueryParam(BaseModel):
    """用户档案查询参数。"""

    id: int | None = Field(
        default=None, description="用户ID", json_schema_extra={"q": "eq"}
    )
    username: str | None = Field(
        default=None, description="登录账号（模糊）", json_schema_extra={"q": "like"}
    )
    cn_name: str | None = Field(
        default=None, description="中文名（模糊）", json_schema_extra={"q": "like"}
    )
    en_name: str | None = Field(
        default=None, description="英文名（模糊）", json_schema_extra={"q": "like"}
    )
    rank_id: int | None = Field(
        default=None, description="职级ID", json_schema_extra={"q": "eq"}
    )
    position_name: str | None = Field(
        default=None, description="职位名称（模糊）", json_schema_extra={"q": "like"}
    )
    status: str | None = Field(
        default=None, description="在职状态：1在职 0离职", json_schema_extra={"q": "eq"}
    )
    allow_login: str | None = Field(
        default=None, description="是否允许登录", json_schema_extra={"q": "eq"}
    )
    deleted: int | None = Field(
        default=None, description="逻辑删除：0否 1是", json_schema_extra={"q": "eq"}
    )
    org_id: int | None = Field(
        default=None,
        description="按组织过滤：只返回在该组织（或其子树）有归属的用户",
    )
    include_sub: bool = Field(
        default=False, description="org_id 过滤是否包含所有下级组织"
    )
    team_id: int | None = Field(
        default=None, description="按受众团队过滤：只返回该团队的成员"
    )
