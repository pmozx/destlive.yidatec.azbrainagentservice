"""结果台账数据层（表 ``sbp_ai_system_request_log``）。

只做两件事：按 ``request_id`` 读写台账行，以及过期行清理。
状态机的语义在 ``service.py``，契约错误码映射在 ``modules_api/base``。
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_crud import CRUDBase

from .model import SystemRequestLogModel


class SystemRequestLogCRUD(CRUDBase[SystemRequestLogModel, BaseModel, BaseModel]):
    """结果台账数据层（按 request_id 定位，不复用主键批量接口）。"""

    def __init__(self, db: AsyncSession) -> None:
        super().__init__(model=SystemRequestLogModel, db=db)

    async def get_by_request_id(self, request_id: str) -> SystemRequestLogModel | None:
        """按契约请求 ID 取台账行（未登记返回 None）。"""
        result = await self.db.execute(
            select(SystemRequestLogModel).where(SystemRequestLogModel.request_id == request_id)
        )
        return result.scalars().first()

    async def finish(
        self,
        *,
        request_id: str,
        status: str,
        result: dict[str, Any] | None = None,
        result_visible: bool = True,
        error_code: str | None = None,
    ) -> int:
        """把运行中的台账收尾（``succeeded`` / ``failed``），返回命中行数。"""
        result_row = await self.db.execute(
            update(SystemRequestLogModel)
            .where(SystemRequestLogModel.request_id == request_id)
            .values(status=status, result_ref=result, result_visible=result_visible, error_code=error_code)
        )
        await self.db.flush()
        return int(getattr(result_row, "rowcount", 0) or 0)

    async def purge_expired(self, now: datetime) -> int:
        """物理清理已过期台账，返回清理条数（启动自检调用）。"""
        result = await self.db.execute(delete(SystemRequestLogModel).where(SystemRequestLogModel.expires_at < now))
        await self.db.flush()
        return int(getattr(result, "rowcount", 0) or 0)
