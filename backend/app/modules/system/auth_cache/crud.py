"""授权缓存数据层。"""

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_crud import CRUDBase

from .model import SystemAuthCacheModel


class SystemAuthCacheCRUD(CRUDBase[SystemAuthCacheModel, BaseModel, BaseModel]):
    """授权缓存数据层。"""

    def __init__(self, db: AsyncSession) -> None:
        super().__init__(model=SystemAuthCacheModel, db=db)
