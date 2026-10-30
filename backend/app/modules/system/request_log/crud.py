"""结果台账数据层。"""

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_crud import CRUDBase

from .model import SystemRequestLogModel


class SystemRequestLogCRUD(CRUDBase[SystemRequestLogModel, BaseModel, BaseModel]):
    """结果台账数据层（只读为主；结果由契约层写入）。"""

    def __init__(self, db: AsyncSession) -> None:
        super().__init__(model=SystemRequestLogModel, db=db)
