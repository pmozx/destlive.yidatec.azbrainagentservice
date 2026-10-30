"""防重放台账数据层：原子登记与过期清理。

原子性由 ``uq_sbp_ai_system_nonce_client_nonce`` + ``ON CONFLICT DO UPDATE ... WHERE expires_at < now``
保证：返回行 = 本次首用（新插入或接管过期行）；无返回行 = 窗口内重复 = 重放。
"""

from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_crud import CRUDBase

from .model import SystemNonceModel

_NONCE_CONSTRAINT = "uq_sbp_ai_system_nonce_client_nonce"


class SystemNonceCRUD(CRUDBase[SystemNonceModel, BaseModel, BaseModel]):
    """nonce 台账数据层。"""

    def __init__(self, db: AsyncSession) -> None:
        super().__init__(model=SystemNonceModel, db=db)

    async def register(
        self,
        *,
        client_id: str,
        nonce: str,
        request_id: str | None,
        now: datetime,
        expires_at: datetime,
    ) -> bool:
        """首用返回 True；窗口内重复返回 False（不提交，事务由 Service 负责）。"""
        statement = (
            pg_insert(self.model)
            .values(client_id=client_id, nonce=nonce, request_id=request_id, created_time=now, expires_at=expires_at)
            .on_conflict_do_update(
                constraint=_NONCE_CONSTRAINT,
                set_={"request_id": request_id, "created_time": now, "expires_at": expires_at},
                # 只有已过期的旧行才允许接管；未过期说明是同一个 nonce 的第二次到达
                where=self.model.expires_at < now,
            )
            .returning(self.model.id)
        )
        return (await self.db.execute(statement)).first() is not None

    async def purge_expired(self, now: datetime) -> int:
        """物理清理过期 nonce，返回清理条数。"""
        result = await self.db.execute(delete(self.model).where(self.model.expires_at < now))
        return int(result.rowcount or 0)
