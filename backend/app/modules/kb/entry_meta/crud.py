"""知识条目元数据数据层。"""

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_crud import CRUDBase

from .model import KbEntryMetaModel


class KbEntryMetaCRUD(CRUDBase[KbEntryMetaModel, BaseModel, BaseModel]):
    """知识条目元数据数据层。"""

    def __init__(self, db: AsyncSession) -> None:
        super().__init__(model=KbEntryMetaModel, db=db)
