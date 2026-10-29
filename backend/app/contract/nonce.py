"""nonce 防重放台账（T1.1）：把 POC 的进程内字典换成持久化 + 原子登记。

为什么必须落库（POC 结论 §二.1 的「待正式化」）：
1. 进程内字典在多副本部署下各存一份，重放打到另一实例就漏判；重启后清零，窗口内还能再放一次；
2. 「先查后插」在并发重放下两个请求会同时通过检查：首用的唯一事实来源必须是
   `uq_sbp_ai_system_nonce_client_nonce` 这个唯一约束；
3. 单条 SQL 完成「插入 / 已过期则接管 / 未过期则不动」，不需要额外锁：

   ``INSERT ... ON CONFLICT (client_id, nonce) DO UPDATE ... WHERE expires_at < now RETURNING id``

   返回行 = 本次首用（新插入或接管过期行）；无返回行 = 窗口内重复 = 重放。

保留时长取 `CONTRACT_NONCE_TTL_SECONDS`（默认 600s ≥ 2×时钟窗口）：nonce 只需在
「时间戳仍可能通过窗口校验」的时段内有效，过期行可被安全接管，由 `purge_expired` 物理清理。
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError

from app.config.setting import settings
from app.core.database import async_session_factory
from app.core.logger import logger
from app.modules.system.models import SystemNonceModel

from .errors import AUTHORIZATION_UNAVAILABLE, ContractApiError

_NONCE_CONSTRAINT = "uq_sbp_ai_system_nonce_client_nonce"


class NonceStore:
    """nonce 原子登记：一次往返内完成判重与写入，跨实例共享。"""

    def __init__(self, *, ttl_seconds: int | None = None) -> None:
        self._ttl_seconds = ttl_seconds if ttl_seconds is not None else settings.CONTRACT_NONCE_TTL_SECONDS

    async def register(self, *, client_id: str, nonce: str, request_id: str | None, current_ms: int) -> bool:
        """首用返回 True；窗口内重复（重放）返回 False。

        独立会话 + 立即提交：nonce 必须在业务处理**之前**就对其他实例可见，否则并发重放会同时通过。
        """
        now = datetime.fromtimestamp(current_ms / 1000, tz=UTC)
        expires_at = now + timedelta(seconds=self._ttl_seconds)
        statement = (
            pg_insert(SystemNonceModel)
            .values(client_id=client_id, nonce=nonce, request_id=request_id, created_time=now, expires_at=expires_at)
            .on_conflict_do_update(
                constraint=_NONCE_CONSTRAINT,
                set_={"request_id": request_id, "created_time": now, "expires_at": expires_at},
                # 只有已过期的旧行才允许接管；未过期说明是同一个 nonce 的第二次到达
                where=SystemNonceModel.expires_at < now,
            )
            .returning(SystemNonceModel.id)
        )
        try:
            async with async_session_factory() as session:
                result = await session.execute(statement)
                row = result.first()
                await session.commit()
        except SQLAlchemyError as exc:
            # 台账不可用时不能放行（放行等于关掉防重放），按「授权依赖不可用」503 处理
            logger.error("nonce 登记失败，拒绝本次请求: client_id={} err={}", client_id, exc)
            raise ContractApiError(AUTHORIZATION_UNAVAILABLE, "签名防重放台账暂不可用，请稍后重试。") from exc

        if row is None:
            logger.warning("nonce 重放被拒: client_id={} nonce={} request_id={}", client_id, nonce, request_id)
            return False
        return True

    async def purge_expired(self, *, current_ms: int | None = None) -> int:
        """物理清理过期 nonce（启动时与巡检调用），返回清理条数。"""
        now = (
            datetime.fromtimestamp(current_ms / 1000, tz=UTC)
            if current_ms is not None
            else datetime.now(UTC)
        )
        try:
            async with async_session_factory() as session:
                result = await session.execute(delete(SystemNonceModel).where(SystemNonceModel.expires_at < now))
                await session.commit()
        except SQLAlchemyError as exc:
            logger.warning("过期 nonce 清理失败（不影响验签）: {}", exc)
            return 0
        return int(result.rowcount or 0)


# 模块级单例：无状态（TTL 来自配置），依赖注入与测试都直接复用
nonce_store = NonceStore()
