"""GSI 用户有效性出入参 Schema。"""

from pydantic import BaseModel, ConfigDict, Field


class SbpSysUserOutSchema(BaseModel):
    """用户有效性输出（仅契约允许字段）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="用户ID")
    status: str = Field(..., description="在职状态：1在职 0离职")
    allow_login: str = Field(..., description="是否允许登录：1允许 0禁止")
    deleted: int = Field(..., description="逻辑删除：0否 1是")


class SbpSysUserQueryParam(BaseModel):
    """用户有效性查询参数。"""

    id: int | None = Field(
        default=None, description="用户ID", json_schema_extra={"q": "eq"}
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
