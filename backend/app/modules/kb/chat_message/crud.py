"""会话消息数据层：按会话取正文、按会话清空、按 ``uuid`` 回查单条。

- 消息按 ``id`` 升序追加，与契约 ``created_at`` 单调一致，分页用偏移量足够稳
  （翻页期间新增只会落到下一页之后，不会重复或丢失）。
- 依赖失效**不在写入时判定**：失效（换指针、停用、撤权）是写入之后才发生的事，
  库里只留当时的 ``knowledge_deps`` 证据，读的时候逐条比对当前指针。
- 清空历史置 ``deleted`` 而不物理删：契约 #15 的超时查询可能在清空后才到，
  没有行就无法回答「这次请求当时是否成功」，也无法把已清空的结果正确藏起来。
"""

from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_crud import CRUDBase

from .model import KbChatMessageModel
from .schema import MESSAGE_DELETED


class KbChatMessageCRUD(CRUDBase[KbChatMessageModel, BaseModel, BaseModel]):
    """会话消息数据层。"""

    def __init__(self, db: AsyncSession) -> None:
        super().__init__(model=KbChatMessageModel, db=db)

    # ── 查询 ──────────────────────────────────────────────────────────

    async def get_by_message_id(self, message_id: str) -> KbChatMessageModel | None:
        """按契约 ``message_id``（= ``uuid``）查一行（#15 复核结果可见性用）。"""
        return await self.get(uuid=message_id)

    async def count_visible(self, session_id: str) -> int:
        """统计未删除消息数（``hidden`` 也算存在：它只是不展示，不是被清空）。"""
        result = await self.db.execute(
            select(func.count())
            .select_from(KbChatMessageModel)
            .where(
                KbChatMessageModel.session_id == session_id,
                KbChatMessageModel.status != MESSAGE_DELETED,
            )
        )
        return int(result.scalar() or 0)

    async def list_visible(
        self,
        *,
        session_id: str,
        offset: int,
        limit: int,
        ascending: bool = True,
    ) -> list[KbChatMessageModel]:
        """按会话取一页未删除消息；``ascending`` 决定 #10 历史的正序/反序。"""
        column = KbChatMessageModel.id.asc() if ascending else KbChatMessageModel.id.desc()
        result = await self.db.execute(
            select(KbChatMessageModel)
            .where(
                KbChatMessageModel.session_id == session_id,
                KbChatMessageModel.status != MESSAGE_DELETED,
            )
            .order_by(column)
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def recent_visible(self, *, session_id: str, limit: int) -> list[KbChatMessageModel]:
        """取最近 ``limit`` 条未删除消息，返回**正序**（给多轮上下文用）。

        先按 ``id`` 倒序截断再反转：``limit`` 是「最近几轮」，正序 LIMIT 会取到最早的
        几条，把上下文永远钉在会话开头。
        """
        if limit <= 0:
            return []
        result = await self.db.execute(
            select(KbChatMessageModel)
            .where(
                KbChatMessageModel.session_id == session_id,
                KbChatMessageModel.status != MESSAGE_DELETED,
            )
            .order_by(KbChatMessageModel.id.desc())
            .limit(limit)
        )
        return list(reversed(list(result.scalars().all())))

    # ── 状态变更 ──────────────────────────────────────────────────────

    async def mark_session_deleted(self, session_id: str) -> int:
        """清空/删除会话时把该会话所有消息置 ``deleted``，返回影响行数。"""
        result = await self.db.execute(
            update(KbChatMessageModel)
            .where(
                KbChatMessageModel.session_id == session_id,
                KbChatMessageModel.status != MESSAGE_DELETED,
            )
            .values(status=MESSAGE_DELETED)
        )
        await self.db.flush()
        return int(result.rowcount or 0)
