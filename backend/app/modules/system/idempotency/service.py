"""幂等台账服务：预约状态机、结果登记、失败释放与过期清理。

服务只返回中性状态（``new`` / ``replay`` / ``body_conflict`` / ``processing``），
由调用层翻译成契约错误码，避免系统域反向依赖 ``app.modules_api.base``。

台账与业务同库不同事务：登记/完成都用独立会话立即提交；业务失败必须显式 release，
否则一次失败会把该 ID 永久锁在 ``processing``（重试全变 409）。
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from app.config.setting import settings
from app.core.database import async_session_factory
from app.core.logger import logger

from .crud import STATUS_PROCESSING, SystemIdempotencyCRUD

STATE_NEW = "new"
STATE_REPLAY = "replay"
STATE_BODY_CONFLICT = "body_conflict"
STATE_PROCESSING = "processing"


@dataclass(frozen=True)
class IdempotencyOutcome:
    """一次幂等预约的中性结果；``replay`` 时携带首次结果摘要。"""

    state: str
    result: dict[str, Any] | None = None

    @property
    def is_replay(self) -> bool:
        return self.state == STATE_REPLAY


class SystemIdempotencyService:
    """幂等台账服务（无请求级会话依赖）。"""

    async def reserve(
        self,
        *,
        scope: str,
        biz_id: str,
        body_hash: str,
        ttl_days: int | None = None,
    ) -> IdempotencyOutcome:
        """预约幂等键：首用 ``new``，命中已完成记录 ``replay``，异正文/处理中返回冲突状态。"""
        now = datetime.now(UTC)
        expires_at = now + timedelta(days=ttl_days if ttl_days is not None else settings.CONTRACT_IDEMPOTENCY_TTL_DAYS)

        try:
            async with async_session_factory() as session:
                crud = SystemIdempotencyCRUD(session)
                if await crud.insert_reservation(
                    scope=scope, biz_id=biz_id, body_hash=body_hash, now=now, expires_at=expires_at
                ):
                    await session.commit()
                    return IdempotencyOutcome(STATE_NEW)

                row = await crud.get_by_biz_id(scope, biz_id)
                if row is None:
                    # 极端竞态（刚被 release 掉）：按首用重试一次，不再递归
                    await session.rollback()
                elif row.body_hash != body_hash:
                    await session.rollback()
                    return IdempotencyOutcome(STATE_BODY_CONFLICT)
                elif row.expires_at is not None and row.expires_at < now:
                    # 台账已过期：按新请求处理（Q11 建议口径），用条件更新避免并发抢占
                    if not await crud.take_over_expired(
                        scope=scope, biz_id=biz_id, body_hash=body_hash, now=now, expires_at=expires_at
                    ):
                        await session.rollback()
                        return IdempotencyOutcome(STATE_PROCESSING)
                    await session.commit()
                    return IdempotencyOutcome(STATE_NEW)
                elif row.status == STATUS_PROCESSING:
                    await session.rollback()
                    return IdempotencyOutcome(STATE_PROCESSING)
                else:
                    await session.commit()
                    return IdempotencyOutcome(STATE_REPLAY, row.result_ref)
        except SQLAlchemyError as exc:
            logger.error("幂等台账不可用，拒绝写入请求: scope={} biz_id={} err={}", scope, biz_id, exc)
            raise

        # row is None 的竞态分支：按新请求重新预约一次（幂等键刚被释放，重试必然首用）
        return await self.reserve(scope=scope, biz_id=biz_id, body_hash=body_hash, ttl_days=ttl_days)

    async def complete(
        self,
        *,
        scope: str,
        biz_id: str,
        body_hash: str,
        result: dict[str, Any] | None = None,
        tombstone: bool = False,
    ) -> None:
        """登记首次结果；``tombstone=true`` 用于删除类（永不清理）。"""
        now = datetime.now(UTC)
        expires_at = None if tombstone else now + timedelta(days=settings.CONTRACT_IDEMPOTENCY_TTL_DAYS)
        try:
            async with async_session_factory() as session:
                await SystemIdempotencyCRUD(session).complete(
                    scope=scope,
                    biz_id=biz_id,
                    body_hash=body_hash,
                    result=result,
                    tombstone=tombstone,
                    expires_at=expires_at,
                )
                await session.commit()
        except SQLAlchemyError as exc:
            # 结果登记失败不影响已成功的业务响应，但要留日志：该 ID 后续重试会拿到 409
            logger.error("幂等结果登记失败: scope={} biz_id={} err={}", scope, biz_id, exc)

    async def release(self, *, scope: str, biz_id: str, body_hash: str) -> None:
        """失败回滚：删除预约，让同一 ID 的后续重试能重新进入处理（墓碑不受影响）。"""
        try:
            async with async_session_factory() as session:
                await SystemIdempotencyCRUD(session).release(scope=scope, biz_id=biz_id, body_hash=body_hash)
                await session.commit()
        except SQLAlchemyError as exc:
            logger.warning("幂等预约释放失败（该 ID 可能被锁在 processing）: scope={} biz_id={} err={}", scope, biz_id, exc)

    async def purge_expired(self, *, current_ms: int | None = None) -> int:
        """物理清理过期台账（墓碑不清），返回清理条数；失败只告警。"""
        now = datetime.fromtimestamp(current_ms / 1000, tz=UTC) if current_ms is not None else datetime.now(UTC)
        try:
            async with async_session_factory() as session:
                deleted = await SystemIdempotencyCRUD(session).purge_expired(now)
                await session.commit()
        except SQLAlchemyError as exc:
            logger.warning("过期幂等台账清理失败（不影响业务）: {}", exc)
            return 0
        return deleted
