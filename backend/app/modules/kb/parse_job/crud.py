"""解析任务数据层。"""

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_crud import CRUDBase

from .model import KbParseJobModel


class KbParseJobCRUD(CRUDBase[KbParseJobModel, BaseModel, BaseModel]):
    """解析任务数据层。"""

    def __init__(self, db: AsyncSession) -> None:
        super().__init__(model=KbParseJobModel, db=db)
