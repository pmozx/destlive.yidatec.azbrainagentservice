"""防重放台账服务：独立事务的原子登记与过期清理。

为什么用独立会话 + 立即提交：nonce 必须在业务处理**之前**就对其他实例可见，
否则并发重放仍会同时通过；这也让 Service 可以被契约层、启动自检复用。
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import SQLAlchemyError

from app.config.setting import settings
from app.core.database import async_session_factory
from app.core.logger import logger

from .crud import SystemNonceCRUD


class SystemNonceService:
    """nonce 台账服务（无请求级会话依赖）。"""

    def __init__(self, *, ttl_seconds: int | None = None) -> None:
        self._ttl_seconds = ttl_seconds if ttl_seconds is not None else settings.CONTRACT_NONCE_TTL_SECONDS

    async def register(self, *, client_id: str, nonce: str, request_id: str | None, current_ms: int) -> bool:
        """首用返回 True；窗口内重复（重放）返回 False。

        数据库不可用时直接把 ``SQLAlchemyError`` 抛给调用方翻译成业务错误码 ——
        台账不可用绝不能放行（放行等于把防重放关掉）。
        """
        now = datetime.fromtimestamp(current_ms / 1000, tz=UTC)
        expires_at = now + timedelta(seconds=self._ttl_seconds)
        try:
            async with async_session_factory() as session:
                first_use = await SystemNonceCRUD(session).register(
                    client_id=client_id,
                    nonce=nonce,
                    request_id=request_id,
                    now=now,
                    expires_at=expires_at,
                )
                await session.commit()
        except SQLAlchemyError as exc:
            logger.error("nonce 登记失败，拒绝本次请求: client_id={} err={}", client_id, exc)
            raise

        if not first_use:
            logger.warning("nonce 重放被拒: client_id={} nonce={} request_id={}", client_id, nonce, request_id)
        return first_use

    async def purge_expired(self, *, current_ms: int | None = None) -> int:
        """物理清理过期 nonce（启动时与巡检调用），返回清理条数；失败只告警。"""
        now = datetime.fromtimestamp(current_ms / 1000, tz=UTC) if current_ms is not None else datetime.now(UTC)
        try:
            async with async_session_factory() as session:
                deleted = await SystemNonceCRUD(session).purge_expired(now)
                await session.commit()
        except SQLAlchemyError as exc:
            logger.warning("过期 nonce 清理失败（不影响验签）: {}", exc)
            return 0
        return deleted
