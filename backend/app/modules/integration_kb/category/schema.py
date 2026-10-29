from pydantic import BaseModel, Field

from app.core.base_schema import BaseQueryParam, BaseSchema


class CategoryCreateSchema(BaseModel):
    """知识分类创建参数"""

    space_id: int = Field(..., ge=1, description="所属空间ID")
    parent_id: int | None = Field(default=None, ge=1, description="父分类ID(为空即一级分类)")
    name: str = Field(..., min_length=1, max_length=128, description="分类名称")
    sort: int = Field(default=0, ge=0, description="排序")
    description: str = Field(default="", description="分类说明")
    status: int = Field(default=0, ge=0, le=1, description="状态(0:启用 1:停用)")


class CategoryUpdateSchema(BaseModel):
    """知识分类修改参数"""

    space_id: int | None = Field(default=None, ge=1, description="所属空间ID")
    parent_id: int | None = Field(default=None, ge=1, description="父分类ID")
    name: str | None = Field(default=None, min_length=1, max_length=128, description="分类名称")
    sort: int | None = Field(default=None, ge=0, description="排序")
    description: str | None = Field(default=None, description="分类说明")
    status: int | None = Field(default=None, ge=0, le=1, description="状态")


class CategoryOutSchema(BaseSchema):
    """知识分类树节点"""

    space_id: int = Field(..., description="所属空间ID")
    parent_id: int | None = Field(default=None, description="父分类ID")
    name: str = Field(..., description="分类名称")
    sort: int = Field(default=0, description="排序")
    description: str = Field(default="", description="分类说明")
    doc_count: int = Field(default=0, description="直属文档数")
    status: int = Field(default=0, description="状态")
    children: list["CategoryOutSchema"] = Field(default_factory=list, description="子分类")


class CategoryQueryParam(BaseQueryParam):
    """知识分类查询参数"""

    space_id: int | None = Field(None, ge=1, description="所属空间ID", json_schema_extra={"q": "eq"})
    name: str | None = Field(None, description="分类名称", json_schema_extra={"q": "like"})
