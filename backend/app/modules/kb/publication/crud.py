"""发布单元数据层。"""

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_crud import CRUDBase

from .model import KbPublicationModel


class KbPublicationCRUD(CRUDBase[KbPublicationModel, BaseModel, BaseModel]):
    """发布单元数据层。"""

    def __init__(self, db: AsyncSession) -> None:
        super().__init__(model=KbPublicationModel, db=db)
