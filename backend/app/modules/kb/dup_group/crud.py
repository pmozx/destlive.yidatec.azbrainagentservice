"""重复分组数据层。"""

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_crud import CRUDBase

from .model import KbDupGroupModel


class KbDupGroupCRUD(CRUDBase[KbDupGroupModel, BaseModel, BaseModel]):
    """重复分组数据层。"""

    def __init__(self, db: AsyncSession) -> None:
        super().__init__(model=KbDupGroupModel, db=db)
