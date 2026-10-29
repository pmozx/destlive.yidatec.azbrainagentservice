"""集成-AI 问答-会话与消息数据层。"""

from collections.abc import Sequence

from sqlalchemy import delete, false, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_crud import CRUDBase

from .model import IntegrationChatMessageModel, IntegrationChatSessionModel
from .schema import MessageCreateSchema


class IntegrationChatSessionCRUD(CRUDBase[IntegrationChatSessionModel, MessageCreateSchema, MessageCreateSchema]):
    """问答会话数据层（本域无 RBAC，不做归属人过滤）"""

    def __init__(self, db: AsyncSession) -> None:
        super().__init__(model=IntegrationChatSessionModel, db=db)


class IntegrationChatMessageCRUD(CRUDBase[IntegrationChatMessageModel, MessageCreateSchema, MessageCreateSchema]):
    """问答消息数据层"""

    def __init__(self, db: AsyncSession) -> None:
        super().__init__(model=IntegrationChatMessageModel, db=db)

    async def get_by_session(self, session_id: int) -> Sequence[IntegrationChatMessageModel]:
        """按会话取全部消息（时间升序，供历史回显）。"""
        sql = (
            select(self.model)
            .where(self.model.is_deleted == false(), self.model.session_id == session_id)
            .order_by(self.model.created_time.asc(), self.model.id.asc())
        )
        result = await self.db.execute(sql)
        return result.scalars().all()

    async def get_recent(self, session_id: int, limit: int) -> Sequence[IntegrationChatMessageModel]:
        """取会话最近 N 条消息（倒序取再翻正，供多轮指代消解）。"""
        if limit <= 0:
            return []
        sql = (
            select(self.model)
            .where(self.model.is_deleted == false(), self.model.session_id == session_id)
            .order_by(self.model.created_time.desc(), self.model.id.desc())
            .limit(limit)
        )
        result = await self.db.execute(sql)
        return list(reversed(result.scalars().all()))

    async def delete_by_session(self, session_ids: list[int]) -> None:
        """按会话批量删除消息（会话删除时显式清理，不依赖数据库级联开关）。

        物理删除：本表没有 ``deleted_id``，CRUDBase 的软删判定不成立，
        会话本身也是物理删除，两者口径保持一致才不会留下孤儿消息。
        """
        if not session_ids:
            return
        await self.db.execute(delete(self.model).where(self.model.session_id.in_(session_ids)))
        await self.db.flush()
