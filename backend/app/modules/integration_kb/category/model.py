"""集成-知识库-知识分类模型（表 sbp_ai_integration_kb_category）。"""

from sqlalchemy import Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_model import ModelMixin

INTEGRATION_KB_CATEGORY_TABLE_NAME: str = "sbp_ai_integration_kb_category"


class IntegrationKbCategoryModel(ModelMixin):
    """知识分类表（空间内二级树：parent_id 为空即一级分类）

    最多两级：服务层校验父节点必须是一级分类，避免前端树渲染与拖拽语义失控。
    """

    __tablename__: str = INTEGRATION_KB_CATEGORY_TABLE_NAME
    __table_args__: tuple = (
        Index("ix_sbp_ai_integration_kb_category_space_parent", "space_id", "parent_id"),
        {"comment": "集成-知识库-知识分类表"},
    )

    space_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True, comment="所属空间ID")
    parent_id: Mapped[int | None] = mapped_column(Integer, default=None, nullable=True, index=True, comment="父分类ID(一级分类为空)")
    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="分类名称")
    sort: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="排序(升序)")
    description: Mapped[str] = mapped_column(Text, default="", nullable=False, comment="分类说明")
    status: Mapped[int] = mapped_column(Integer, default=0, nullable=False, index=True, comment="状态(0:启用 1:停用)")
