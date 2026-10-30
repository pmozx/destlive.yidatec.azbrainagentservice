"""幂等台账数据层：预约、结果登记、释放与过期清理。

所有方法都只操作调用方传入的会话，不自行提交；事务边界由 Service 统一管理。
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_crud import CRUDBase

from .model import SystemIdempotencyModel

_BIZ_CONSTRAINT = "uq_sbp_ai_system_idempotency_scope_biz"

STATUS_PROCESSING = "processing"
STATUS_SUCCEEDED = "succeeded"
STATUS_TOMBSTONE = "tombstone"


class SystemIdempotencyCRUD(CRUDBase[SystemIdempotencyModel, BaseModel, BaseModel]):
    """幂等台账数据层。"""

    def __init__(self, db: AsyncSession) -> None:
        super().__init__(model=SystemIdempotencyModel, db=db)

    async def insert_reservation(
        self,
        *,
        scope: str,
        biz_id: str,
        body_hash: str,
        now: datetime,
        expires_at: datetime,
    ) -> bool:
        """原子预约：插入成功返回 True，已存在（冲突）返回 False。"""
        inserted = await self.db.execute(
            pg_insert(self.model)
            .values(
                scope=scope,
                biz_id=biz_id,
                body_hash=body_hash,
                status=STATUS_PROCESSING,
                created_time=now,
                expires_at=expires_at,
            )
            .on_conflict_do_nothing(constraint=_BIZ_CONSTRAINT)
            .returning(self.model.id)
        )
        return inserted.first() is not None

    async def get_by_biz_id(self, scope: str, biz_id: str) -> SystemIdempotencyModel | None:
        row = (
            await self.db.execute(
                select(self.model).where(self.model.scope == scope, self.model.biz_id == biz_id)
            )
        ).scalar_one_or_none()
        return row

    async def get_by_request_id(self, request_id: str, limit: int) -> Sequence[SystemIdempotencyModel]:
        """按 request_id 追踪幂等记录：精确匹配 + ``request_id-*`` 前缀补齐。

        ``biz_id`` 是业务键（job_id / request_id / event_id），job / event 域常见
        ``request_id-job_id`` 形态，只按等值查询会漏掉。
        """
        return await self.get_list(
            order_by=[{"id": "desc"}],
            limit=limit,
            extra_conditions=[
                or_(
                    self.model.biz_id == request_id,
                    self.model.biz_id.like(f"{request_id}-%"),
                )
            ],
        )

    async def take_over_expired(
        self,
        *,
        scope: str,
        biz_id: str,
        body_hash: str,
        now: datetime,
        expires_at: datetime,
    ) -> bool:
        """接管已过期台账（条件更新，避免并发抢占）；未抢到返回 False。"""
        taken = await self.db.execute(
            update(self.model)
            .where(
                self.model.scope == scope,
                self.model.biz_id == biz_id,
                self.model.expires_at.is_not(None),
                self.model.expires_at < now,
            )
            .values(
                body_hash=body_hash,
                status=STATUS_PROCESSING,
                result_ref=None,
                created_time=now,
                expires_at=expires_at,
            )
            .returning(self.model.id)
        )
        return taken.first() is not None

    async def complete(
        self,
        *,
        scope: str,
        biz_id: str,
        body_hash: str,
        result: dict[str, Any] | None,
        tombstone: bool,
        expires_at: datetime | None,
    ) -> None:
        """登记首次结果；``tombstone=true`` 时 ``expires_at`` 传 NULL（永不清理）。"""
        await self.db.execute(
            update(self.model)
            .where(
                self.model.scope == scope,
                self.model.biz_id == biz_id,
                self.model.body_hash == body_hash,
            )
            .values(
                status=STATUS_TOMBSTONE if tombstone else STATUS_SUCCEEDED,
                result_ref=result,
                expires_at=expires_at,
            )
        )

    async def release(self, *, scope: str, biz_id: str, body_hash: str) -> None:
        """删除未完成的预约（墓碑记录不受影响）。"""
        await self.db.execute(
            delete(self.model).where(
                self.model.scope == scope,
                self.model.biz_id == biz_id,
                self.model.body_hash == body_hash,
                self.model.status != STATUS_TOMBSTONE,
            )
        )

    async def purge_expired(self, now: datetime) -> int:
        """物理清理过期台账（墓碑 ``expires_at IS NULL`` 不清），返回清理条数。"""
        result = await self.db.execute(
            delete(self.model).where(
                self.model.expires_at.is_not(None),
                self.model.expires_at < now,
            )
        )
        return int(result.rowcount or 0)
