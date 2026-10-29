from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_crud import CRUDBase

from .model import IntegrationKbCategoryModel
from .schema import CategoryCreateSchema, CategoryUpdateSchema


class IntegrationKbCategoryCRUD(CRUDBase[IntegrationKbCategoryModel, CategoryCreateSchema, CategoryUpdateSchema]):
    """知识分类数据层"""

    def __init__(self, db: AsyncSession) -> None:
        super().__init__(model=IntegrationKbCategoryModel, db=db)
