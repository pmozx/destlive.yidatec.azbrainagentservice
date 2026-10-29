from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_crud import CRUDBase

from .model import IntegrationKbSpaceModel
from .schema import SpaceCreateSchema, SpaceUpdateSchema


class IntegrationKbSpaceCRUD(CRUDBase[IntegrationKbSpaceModel, SpaceCreateSchema, SpaceUpdateSchema]):
    """知识空间数据层"""

    def __init__(self, db: AsyncSession) -> None:
        super().__init__(model=IntegrationKbSpaceModel, db=db)
