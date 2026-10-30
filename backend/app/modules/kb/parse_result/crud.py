"""解析产物数据层。"""

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_crud import CRUDBase

from .model import KbParseResultModel


class KbParseResultCRUD(CRUDBase[KbParseResultModel, BaseModel, BaseModel]):
    """解析产物数据层（不可变，一般不提供更新入口）。"""

    def __init__(self, db: AsyncSession) -> None:
        super().__init__(model=KbParseResultModel, db=db)
