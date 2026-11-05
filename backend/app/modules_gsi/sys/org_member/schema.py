"""GSI 组织成员出入参 Schema。"""

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class SbpSysOrgMemberWithOrgOutSchema(BaseModel):
    """组织成员 + 组织上下文 + 用户有效性。

    组织与受众团队是两套平行体系：本模型只回答「人属于哪个部门」，
    不能反推任何知识可见性（受众团队才是授权单位）。
    """

    model_config = ConfigDict(from_attributes=True)

    org_id: int = Field(..., description="组织ID")
    org_name: str | None = Field(default=None, description="组织名称")
    org_code: str | None = Field(default=None, description="组织编码")
    org_type: str | None = Field(default=None, description="节点类型")
    org_category: str | None = Field(default=None, description="组织分类：admin行政 business业务")
    org_parent_id: int | None = Field(default=None, description="上级组织ID")
    org_status: str | None = Field(default=None, description="组织状态：1启用 0停用")
    org_deleted: int | None = Field(default=None, description="组织逻辑删除：0否 1是")
    org_valid: bool = Field(default=False, description="组织有效：status=1 且 deleted=0")
    user_id: int = Field(..., description="用户ID")
    user_exists: bool = Field(default=False, description="用户是否存在于 sbp_sys_user")
    user_status: str | None = Field(default=None, description="在职状态：1在职 0离职")
    allow_login: str | None = Field(default=None, description="是否允许登录：1允许 0禁止")
    user_deleted: int | None = Field(default=None, description="用户逻辑删除：0否 1是")
    user_valid: bool = Field(default=False, description="用户有效：存在、在职且未删除")

    @model_validator(mode="after")
    def _derive_valid_flags(self) -> "SbpSysOrgMemberWithOrgOutSchema":
        self.org_valid = self.org_status == "1" and self.org_deleted == 0
        self.user_exists = self.user_status is not None
        self.user_valid = (
            self.user_exists and self.user_status == "1" and self.user_deleted == 0
        )
        return self


class SbpSysOrgMemberWithOrgQueryParam(BaseModel):
    """组织成员（含组织上下文）查询参数。"""

    org_id: int | None = Field(default=None, description="组织ID")
    user_id: int | None = Field(default=None, description="用户ID")
    org_category: str | None = Field(default=None, description="组织分类：admin/business")
    org_type: str | None = Field(default=None, description="节点类型")
    user_status: str | None = Field(default=None, description="用户在职状态：1在职 0离职")
    valid_only: bool = Field(
        default=False, description="仅看有效组织（status=1 且 deleted=0）"
    )
