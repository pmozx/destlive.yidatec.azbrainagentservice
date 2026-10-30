"""会话消息数据层。"""

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_crud import CRUDBase

from .model import KbChatMessageModel


class KbChatMessageCRUD(CRUDBase[KbChatMessageModel, BaseModel, BaseModel]):
    """会话消息数据层。"""

    def __init__(self, db: AsyncSession) -> None:
        super().__init__(model=KbChatMessageModel, db=db)
