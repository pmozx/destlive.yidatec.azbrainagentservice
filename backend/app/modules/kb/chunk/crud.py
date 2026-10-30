"""知识切片数据层。"""

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_crud import CRUDBase

from .model import KbChunkModel


class KbChunkCRUD(CRUDBase[KbChunkModel, BaseModel, BaseModel]):
    """知识切片数据层。"""

    def __init__(self, db: AsyncSession) -> None:
        super().__init__(model=KbChunkModel, db=db)
