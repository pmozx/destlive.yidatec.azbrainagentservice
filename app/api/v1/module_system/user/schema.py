from pydantic import (Field, BaseModel, EmailStr, field_validator, ConfigDict, )

from app.core.base_schema import BaseQueryParam, BaseSchema
from app.core.validator import mobile_validator, email_validator


class UserUpdateSchema(BaseModel):
    """基础用户信息"""

    username: str | None = Field(default=None, max_length=32, description="用户名称")
    name: str | None = Field(default=None, max_length=32, description="名称")
    mobile: str | None = Field(default=None, max_length=11, description="手机号")
    email: EmailStr | None = Field(default=None, description="邮箱")
    gender: str | None = Field(default=None, max_length=1, description="性别(0:男 1:女 2:未知)")
    avatar: str | None = Field(default=None, max_length=255, description="头像")

    @field_validator("mobile")
    @classmethod
    def validate_mobile(cls, value: str | None):
        """校验手机号格式"""
        return mobile_validator(value)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str | None):
        """校验邮箱格式"""
        if not value:
            return value
        return email_validator(value)



class UserCreateSchema(BaseModel):
    """新增用户
    """
    username: str | None = Field(default=None, max_length=32, description="用户名")
    password: str | None = Field(default=None, min_length=6, max_length=128, description="密码")
    email: str | None = Field(default=None, max_length=128, description="邮箱")
    mobile: str | None = Field(default=None, max_length=11, description="手机")


class UserOutSchema(BaseSchema):
    """用户管理列表/详情响应（精简版，不含大字段嵌套）"""

    model_config = ConfigDict(arbitrary_types_allowed=True, from_attributes=True)

    id: int = Field(default=0, description="主键ID")
    username: str | None = Field(default=None, max_length=32, description="用户名")
    name: str | None = Field(default=None, max_length=32, description="名称")
    mobile: str | None = Field(default=None, max_length=11, description="手机号")
    email: EmailStr | None = Field(default=None, description="邮箱")
    gender: str | None = Field(default=None, max_length=1, description="性别(0:男 1:女 2:未知)")



class UserQueryParam(BaseQueryParam):
    """用户管理查询参数（继承标准 Mixin）

    支持：
    - 时间范围（BaseQueryParam）
    - 创建人/更新人筛选（UserByQueryParam）
    - 业务字段：用户名、名称、手机号、邮箱、部门、状态
    """

    username: str | None = Field(None, description="用户名", json_schema_extra={"q": "like"})
    mobile: str | None = Field(None, description="手机号", pattern=r"^1[3-9]\d{9}$", json_schema_extra={"q": "eq"})
    email: str | None = Field(
        None,
        description="邮箱",
        pattern=r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$",
        json_schema_extra={"q": "eq"},
    )