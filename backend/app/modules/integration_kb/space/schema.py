from pydantic import BaseModel, Field

from app.core.base_schema import BaseQueryParam, BaseSchema


class SpaceCreateSchema(BaseModel):
    """知识空间创建参数"""

    name: str = Field(..., min_length=1, max_length=128, description="空间名称")
    code: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$", description="空间编码(唯一，创建后不可修改)")
    icon: str = Field(default="", max_length=64, description="图标标识")
    color: str = Field(default="", max_length=32, description="主题色")
    description: str = Field(default="", description="空间说明")
    security_level: int = Field(default=1, ge=1, le=3, description="密级(1:公开 2:内部 3:机密)")
    sort: int = Field(default=0, ge=0, description="排序(升序)")
    status: int = Field(default=0, ge=0, le=1, description="状态(0:启用 1:停用)")


class SpaceUpdateSchema(BaseModel):
    """知识空间修改参数（code 不可修改：改它需级联改文档与分片）"""

    name: str | None = Field(default=None, min_length=1, max_length=128, description="空间名称")
    icon: str | None = Field(default=None, max_length=64, description="图标标识")
    color: str | None = Field(default=None, max_length=32, description="主题色")
    description: str | None = Field(default=None, description="空间说明")
    security_level: int | None = Field(default=None, ge=1, le=3, description="密级")
    sort: int | None = Field(default=None, ge=0, description="排序")
    status: int | None = Field(default=None, ge=0, le=1, description="状态")


class SpaceOutSchema(BaseSchema):
    """知识空间响应模型"""

    name: str = Field(..., description="空间名称")
    code: str = Field(..., description="空间编码")
    icon: str = Field(default="", description="图标标识")
    color: str = Field(default="", description="主题色")
    description: str = Field(default="", description="空间说明")
    security_level: int = Field(default=1, description="密级")
    sort: int = Field(default=0, description="排序")
    doc_count: int = Field(default=0, description="文档数")
    status: int = Field(default=0, description="状态")


class SpaceQueryParam(BaseQueryParam):
    """知识空间查询参数"""

    name: str | None = Field(None, description="空间名称", json_schema_extra={"q": "like"})
    code: str | None = Field(None, description="空间编码", json_schema_extra={"q": "like"})
    status: int | None = Field(None, ge=0, le=1, description="状态", json_schema_extra={"q": "eq"})
