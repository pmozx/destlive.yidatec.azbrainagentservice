"""解析任务数据层。"""

from typing import Any

from pydantic import BaseModel
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_crud import CRUDBase
from app.core.database import async_engine
from app.core.logger import logger

from .model import KbParseJobModel

# 未到达终态的两态：进程重启后这些行的后台任务已不存在，必须改判失败
UNFINISHED_STATUSES: tuple[str, ...] = ("queued", "running")

#: 当前进程是否已补列（多进程各自执行一次，DDL 本身幂等）
_SCHEMA_READY = False

# ``attempt`` 是步骤台账与断点续跑的基础列：存量库不补列，ORM 生成的
# SELECT/UPDATE 会因为缺列直接报错（本工程没有迁移工具，靠启动期幂等补）。
_EXTRA_COLUMNS: tuple[str, ...] = (
    "ALTER TABLE sbp_ai_kb_parse_job ADD COLUMN IF NOT EXISTS attempt INTEGER NOT NULL DEFAULT 1",
)


async def ensure_parse_job_schema() -> None:
    """幂等补齐解析任务表的后续新增列（表不存在时交给建表流程，这里只告警）。"""
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    try:
        async with async_engine.begin() as conn:
            for statement in _EXTRA_COLUMNS:
                await conn.execute(text(statement))
    except Exception as exc:  # noqa: BLE001 建表失败/库不可用时由实际读写给出可读错误
        logger.warning("解析任务表补列失败（attempt 字段可能不可用）: {}", exc)
        return
    _SCHEMA_READY = True


class KbParseJobCRUD(CRUDBase[KbParseJobModel, BaseModel, BaseModel]):
    """解析任务数据层。"""

    def __init__(self, db: AsyncSession) -> None:
        super().__init__(model=KbParseJobModel, db=db)

    async def get(self, preload: list[str | Any] | None = None, **kwargs: Any) -> KbParseJobModel | None:
        """读取前确保补列已完成（受理、查询、后台每一步都走这里）。"""
        await ensure_parse_job_schema()
        return await super().get(preload=preload, **kwargs)

    async def create(self, data: BaseModel | dict[str, Any]) -> KbParseJobModel:
        """写入前确保补列已完成。"""
        await ensure_parse_job_schema()
        return await super().create(data=data)

    async def update_if_attempt(self, *, job_uuid: str, attempt: int, values: dict[str, Any]) -> bool:
        """带 attempt 条件的更新（乐观锁）：返回 False 表示已被新一轮执行接管。

        为什么不用「先读再写」：卡死的旧协程随时可能复活写库，读到的 attempt 在
        UPDATE 提交前就可能过期；条件写在 WHERE 里才由数据库保证互斥。
        """
        result = await self.db.execute(
            update(self.model)
            .where(self.model.uuid == job_uuid, self.model.attempt == attempt)
            .values(**values)
        )
        return bool(result.rowcount)

    async def fail_unfinished(self, *, error_code: str, error_message: str, progress: int) -> int:
        """把所有未到终态的任务批量改判失败，返回影响行数（启动期中断恢复用）。"""
        result = await self.db.execute(
            update(self.model)
            .where(self.model.status.in_(UNFINISHED_STATUSES))
            .values(status="failed", progress=progress, error_code=error_code, error_message=error_message)
        )
        return int(result.rowcount or 0)
