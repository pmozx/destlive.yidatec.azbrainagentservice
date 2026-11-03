"""问答会话数据层：按 ``uuid``（即契约 ``session_id``）定位 + 归属过滤 + 行锁重读。

三个刻意的取舍：

- **定位键用 ``uuid`` 而不是主键**：契约里的 ``session_id`` 就是本表 ``uuid``，用主键
  要多做一层映射，也容易把自增 ID 当会话标识泄漏出去。
- **软删只靠 ``status``**：基座不提供软删除（见 ``ModelMixin``），本表也没有
  ``is_deleted`` 列。查询必须显式带 ``status != deleted``，否则「不加条件」的默认查询
  会把已删会话又漏回来。
- **``lock_owned`` 是 ``SELECT ... FOR UPDATE``**：生成回答要跨 LLM 调用，写回时必须
  用行锁重读，才能判定「等待期间会话被删除/清空」，从而丢弃迟到的回答。
"""

from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_crud import CRUDBase

from .model import KbChatSessionModel
from .schema import SESSION_CLEARED, SESSION_DELETED


class KbChatSessionCRUD(CRUDBase[KbChatSessionModel, BaseModel, BaseModel]):
    """问答会话数据层。"""

    def __init__(self, db: AsyncSession) -> None:
        super().__init__(model=KbChatSessionModel, db=db)

    # ── 查询 ──────────────────────────────────────────────────────────

    async def get_by_session_id(self, session_id: str) -> KbChatSessionModel | None:
        """按契约 ``session_id``（= ``uuid``）查一行，不区分归属与状态（管理端用）。"""
        return await self.get(uuid=session_id)

    async def get_owned(self, *, session_id: str, user_id: int) -> KbChatSessionModel | None:
        """按 ``session_id + user_id`` 查自己的、未删除的会话；查不到一律由上层转 404。"""
        return await self.get(uuid=session_id, user_id=user_id, status=("!=", SESSION_DELETED))

    async def lock_owned(self, *, session_id: str, user_id: int) -> KbChatSessionModel | None:
        """加行锁重读自己的会话（生成结果写回前用）。

        不加锁时，「读快照 → 生成 → 写回」之间会话可能已被删除或清空，迟到的回答会
        把会话写活（已删会话复活、已清空历史又多出两条消息）。拿住行锁后重读，
        才能把这两种情况区分成「丢弃」而不是「覆盖」。
        """
        result = await self.db.execute(
            select(KbChatSessionModel)
            .where(
                KbChatSessionModel.uuid == session_id,
                KbChatSessionModel.user_id == user_id,
                KbChatSessionModel.status != SESSION_DELETED,
            )
            .with_for_update()
        )
        return result.scalars().first()

    async def page_owned(self, *, user_id: int, offset: int, limit: int) -> tuple[list[KbChatSessionModel], int]:
        """契约 #09 的一页会话：只看自己的、未删除的，最近活跃优先。

        ``last_active_at`` 允许为空（#08 创建时可能没给），排序用
        ``coalesce(last_active_at, created_time)`` 兜底，避免空值把新会话排到最后。
        """
        active_at = func.coalesce(KbChatSessionModel.last_active_at, KbChatSessionModel.created_time)
        conditions = (
            KbChatSessionModel.user_id == user_id,
            KbChatSessionModel.status != SESSION_DELETED,
        )
        total_result = await self.db.execute(
            select(func.count()).select_from(KbChatSessionModel).where(*conditions)
        )
        rows_result = await self.db.execute(
            select(KbChatSessionModel)
            .where(*conditions)
            .order_by(active_at.desc(), KbChatSessionModel.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(rows_result.scalars().all()), int(total_result.scalar() or 0)

    # ── 状态变更 ──────────────────────────────────────────────────────

    async def mark_cleared(self, *, session_id: str, active_at: datetime) -> None:
        """清空历史：会话壳保留，状态转 ``cleared`` 并归零消息数。"""
        await self.db.execute(
            update(KbChatSessionModel)
            .where(
                KbChatSessionModel.uuid == session_id,
                KbChatSessionModel.status != SESSION_DELETED,
            )
            .values(status=SESSION_CLEARED, message_count=0, last_active_at=active_at)
        )
        await self.db.flush()

    async def mark_deleted(self, *, session_id: str) -> None:
        """软删会话：置 ``deleted``，此后所有按 ``status != deleted`` 的查询都看不到它。"""
        await self.db.execute(
            update(KbChatSessionModel)
            .where(
                KbChatSessionModel.uuid == session_id,
                KbChatSessionModel.status != SESSION_DELETED,
            )
            .values(status=SESSION_DELETED)
        )
        await self.db.flush()
