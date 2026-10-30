"""接口审计日志服务：查询 / 详情 / 删除 / 过期清理。

访问统一走 ``SystemApiLogCRUD``，本层不手写分页或删除 SQL；
建表不在此处（DDL 统一收口在 ``app/modules/system/bootstrap.py``），本层只负责启动期过期清理。
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.config.setting import settings
from app.core.base_schema import PageResultSchema
from app.core.database import async_session_factory
from app.core.exceptions import CustomException
from app.core.logger import logger
from app.utils.common_util import search_to_dict

from .crud import SystemApiLogCRUD
from .model import SystemApiLogModel
from .schema import SystemApiLogDetailOutSchema, SystemApiLogOutSchema, SystemApiLogQueryParam


class SystemApiLogService:
    """接口审计日志查询/清理服务"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.crud = SystemApiLogCRUD(db)

    async def page(
        self,
        page_no: int,
        page_size: int,
        search: SystemApiLogQueryParam | None = None,
        order_by: list[dict[str, str]] | None = None,
    ) -> PageResultSchema[SystemApiLogOutSchema]:
        """分页查询审计日志：默认按时间倒序（最新操作在最前）。"""
        return await self.crud.page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=order_by or [{"id": "desc"}],
            search=search_to_dict(search),
            out_schema=SystemApiLogOutSchema,
        )

    async def detail(self, id: int) -> SystemApiLogDetailOutSchema:
        """查询单条审计详情（含请求/响应体）。"""
        return await self.crud.get_or_404(
            id=id,
            msg="接口审计日志不存在",
            out_schema=SystemApiLogDetailOutSchema,
        )

    async def delete(self, ids: list[int]) -> None:
        """批量删除审计记录。"""
        if not ids:
            raise CustomException(msg="删除失败，删除对象不能为空")
        await self.crud.delete(ids=ids)

    async def clean(self, days: int) -> int:
        """按保留天数物理清理（供管理端手动触发或定时任务调用），返回清理条数。"""
        if days <= 0:
            raise CustomException(msg="保留天数必须大于 0")
        cutoff = datetime.now(UTC) - timedelta(days=days)
        return await self.crud.delete_by_conditions(SystemApiLogModel.created_time < cutoff)

    # ── 启动期生命周期（无请求上下文，类方法）──────────────────────────

    @classmethod
    async def purge_expired(cls, days: int | None = None) -> int:
        """启动时按保留天数清理过期审计记录（无调度器依赖），返回清理条数。"""
        keep_days = settings.API_LOG_RETENTION_DAYS if days is None else days
        if not settings.API_LOG_ENABLE or keep_days <= 0:
            return 0

        cutoff = datetime.now(UTC) - timedelta(days=keep_days)
        try:
            async with async_session_factory() as session:
                deleted = await SystemApiLogCRUD(session).delete_by_conditions(
                    SystemApiLogModel.created_time < cutoff
                )
                await session.commit()
            return deleted
        except Exception:
            # 清理失败不影响启动，残留数据下次启动继续清
            logger.warning("接口审计日志过期清理失败（保留 {} 天）", keep_days, exc_info=True)
            return 0
