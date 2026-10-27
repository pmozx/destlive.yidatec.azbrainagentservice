from datetime import UTC, datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.setting import settings
from app.core.base_schema import PageResultSchema
from app.core.database import async_engine, async_session_factory
from app.core.exceptions import CustomException
from app.core.logger import logger
from app.utils.common_util import search_to_dict

from .crud import OperationLogCRUD
from .model import OperationLogModel
from .schema import OperationLogDetailOutSchema, OperationLogOutSchema, OperationLogQueryParam


class OperationLogService:
    """操作日志查询/清理服务"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def page(
        self,
        page_no: int,
        page_size: int,
        search: OperationLogQueryParam | None = None,
        order_by: list[dict[str, str]] | None = None,
    ) -> PageResultSchema[OperationLogOutSchema]:
        """分页查询审计日志：默认按时间倒序（最新操作在最前）。"""
        result = await OperationLogCRUD(self.db).page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=order_by or [{"id": "desc"}],
            search=search_to_dict(search),
            out_schema=OperationLogOutSchema,
        )
        return result

    async def detail(self, id: int) -> OperationLogDetailOutSchema:
        """查询单条审计详情（含请求/响应体）。"""
        obj = await OperationLogCRUD(self.db).get_or_404(id=id, msg="操作日志不存在")
        return OperationLogDetailOutSchema.model_validate(obj)

    async def delete(self, ids: list[int]) -> None:
        """批量删除审计记录。"""
        if not ids:
            raise CustomException(msg="删除失败，删除对象不能为空")
        await OperationLogCRUD(self.db).delete(ids=ids)

    async def clean(self, days: int) -> int:
        """按保留天数物理清理（供管理端手动触发或定时任务调用），返回清理条数。"""
        if days <= 0:
            raise CustomException(msg="保留天数必须大于 0")
        cutoff = datetime.now(UTC) - timedelta(days=days)
        result = await self.db.execute(delete(OperationLogModel).where(OperationLogModel.created_time < cutoff))
        await self.db.flush()
        return getattr(result, "rowcount", 0) or 0


async def ensure_operation_log_table() -> None:
    """启动时确保审计表存在：只建本表，不触碰其它业务表结构。"""
    if not (settings.OPERATION_LOG_ENABLE and settings.OPERATION_LOG_AUTO_CREATE):
        return
    async with async_engine.begin() as conn:
        await conn.run_sync(OperationLogModel.__table__.create, checkfirst=True)


async def cleanup_expired_operation_logs(days: int | None = None) -> int:
    """启动时按保留天数清理过期审计记录（无调度器依赖），返回清理条数。"""
    keep_days = settings.OPERATION_LOG_RETENTION_DAYS if days is None else days
    if not settings.OPERATION_LOG_ENABLE or keep_days <= 0:
        return 0

    cutoff = datetime.now(UTC) - timedelta(days=keep_days)
    try:
        async with async_session_factory() as session:
            result = await session.execute(delete(OperationLogModel).where(OperationLogModel.created_time < cutoff))
            await session.commit()
        return getattr(result, "rowcount", 0) or 0
    except Exception:
        # 清理失败不影响启动，残留数据下次启动继续清
        logger.warning("操作日志过期清理失败（保留 {} 天）", keep_days, exc_info=True)
        return 0
