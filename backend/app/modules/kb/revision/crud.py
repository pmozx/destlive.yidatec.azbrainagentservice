"""版本历史数据层。"""

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_crud import CRUDBase

from .model import KbRevisionModel


class KbRevisionCRUD(CRUDBase[KbRevisionModel, BaseModel, BaseModel]):
    """版本历史数据层。"""

    def __init__(self, db: AsyncSession) -> None:
        super().__init__(model=KbRevisionModel, db=db)
