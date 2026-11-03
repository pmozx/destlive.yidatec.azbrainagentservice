"""重复检测结果快照数据层：只做数据访问，不含「过期/归属」等业务判定。"""

from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_crud import CRUDBase

from .model import KbDupResultModel


class KbDupResultCRUD(CRUDBase[KbDupResultModel, BaseModel, BaseModel]):
    """``sbp_ai_kb_dup_result`` 数据层。"""

    def __init__(self, db: AsyncSession) -> None:
        super().__init__(model=KbDupResultModel, db=db)

    async def get_by_result_id(self, result_id: str) -> KbDupResultModel | None:
        """按契约 ``result_id`` 读取快照（``result_id`` 落在 ``uuid`` 列，全局唯一）。"""
        result = await self.db.execute(select(KbDupResultModel).where(KbDupResultModel.uuid == result_id).limit(1))
        return result.scalars().first()

    async def purge_expired(self, *, now: datetime) -> int:
        """物理清理过期快照（结果只是 30 分钟的只读缓存，过期即无用）。"""
        return await self.delete_by_conditions(KbDupResultModel.expires_at < now)
