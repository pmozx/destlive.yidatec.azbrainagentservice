"""问答会话数据层。"""

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_crud import CRUDBase

from .model import KbChatSessionModel


class KbChatSessionCRUD(CRUDBase[KbChatSessionModel, BaseModel, BaseModel]):
    """问答会话数据层。"""

    def __init__(self, db: AsyncSession) -> None:
        super().__init__(model=KbChatSessionModel, db=db)
