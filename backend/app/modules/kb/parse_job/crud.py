"""解析任务数据层。"""

from pydantic import BaseModel
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_crud import CRUDBase

from .model import KbParseJobModel

# 未到达终态的两态：进程重启后这些行的后台任务已不存在，必须改判失败
UNFINISHED_STATUSES: tuple[str, ...] = ("queued", "running")


class KbParseJobCRUD(CRUDBase[KbParseJobModel, BaseModel, BaseModel]):
    """解析任务数据层。"""

    def __init__(self, db: AsyncSession) -> None:
        super().__init__(model=KbParseJobModel, db=db)

    async def fail_unfinished(self, *, error_code: str, error_message: str, progress: int) -> int:
        """把所有未到终态的任务批量改判失败，返回影响行数（启动期中断恢复用）。"""
        result = await self.db.execute(
            update(self.model)
            .where(self.model.status.in_(UNFINISHED_STATUSES))
            .values(status="failed", progress=progress, error_code=error_code, error_message=error_message)
        )
        return int(result.rowcount or 0)
