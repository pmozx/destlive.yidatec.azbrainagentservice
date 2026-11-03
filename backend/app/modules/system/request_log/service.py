"""结果台账服务（#15 超时结果查询的承接方）。

与幂等台账（``sbp_ai_system_idempotency``）分工：

- 幂等台账管「同 ``request_id`` 同正文重试直接返回首次结果」，只对**写接口的调用方**可见；
- 本台账管「响应丢了 / 生成超时后按 ``request_id`` 查最终状态」，对**任何调用方**可见，
  且要如实区分 ``pending`` / ``completed`` / ``failed`` / ``cancelled``，过期后按
  ``RESULT_EXPIRED`` 而不是 404。

事务口径与幂等台账一致：台账与业务同库不同事务，登记/收尾都用独立会话立即提交；
台账写失败**只记日志**——不能因为台账不可用把已经成功的业务响应改成 500，
也不能让「查询最终结果」这个兜底能力反过来阻塞主链路。
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.core.database import async_session_factory
from app.core.logger import logger

from .crud import SystemRequestLogCRUD

# ── 操作类别（契约 #15 operation 枚举）──────────────────────────────────────
OPERATION_COMPLETION = "completion"
OPERATION_MUTATION = "mutation"
OPERATION_DELETE = "delete"
OPERATIONS: frozenset[str] = frozenset({OPERATION_COMPLETION, OPERATION_MUTATION, OPERATION_DELETE})

# ── 台账状态（库内取值，与契约 status 不同名，映射在契约层做）────────────────
STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
#: 业务侧主动作废（如生成期间会话被删除 / 清空）：结果不会再产出，也不是技术失败
STATUS_CANCELLED = "cancelled"
STATUS_EXPIRED = "expired"

#: 可查询期限：契约只承诺「超时后可查」，未规定上限；24 小时覆盖一次值班窗口
DEFAULT_TTL_HOURS = 24


@dataclass(frozen=True)
class RequestLogRecord:
    """一次操作的结果台账快照。"""

    request_id: str
    operation: str
    status: str
    result_visible: bool
    result: dict[str, Any] | None
    error_code: str | None
    expires_at: datetime
    created_time: datetime

    @property
    def expired(self) -> bool:
        """是否已过可查询期限（调用方据此回 409 ``RESULT_EXPIRED``）。"""
        expires_at = self.expires_at if self.expires_at.tzinfo else self.expires_at.replace(tzinfo=UTC)
        return datetime.now(UTC) >= expires_at


class SystemRequestLogService:
    """结果台账服务（无请求级会话依赖，自己开事务）。"""

    async def start(
        self,
        *,
        request_id: str,
        operation: str,
        ttl_hours: int = DEFAULT_TTL_HOURS,
    ) -> None:
        """登记「运行中」：同 ID 已存在时保留原行（幂等重放不覆盖首次状态）。"""
        if not request_id:
            return
        now = datetime.now(UTC)
        try:
            async with async_session_factory() as session, session.begin():
                crud = SystemRequestLogCRUD(session)
                if await crud.get_by_request_id(request_id) is not None:
                    return
                await crud.create(
                    data={
                        "request_id": request_id,
                        "operation": operation if operation in OPERATIONS else OPERATION_MUTATION,
                        "status": STATUS_RUNNING,
                        "result_visible": True,
                        "result_ref": None,
                        "error_code": None,
                        "expires_at": now + timedelta(hours=ttl_hours),
                    }
                )
        except IntegrityError:
            # 并发首用：另一路已经登记，按「保留首次」处理
            logger.debug("结果台账并发登记同一 request_id，保留首次行: {}", request_id)
        except SQLAlchemyError as exc:
            logger.error("结果台账登记失败（不影响主链路）: request_id={} err={}", request_id, exc)

    async def finish(
        self,
        *,
        request_id: str,
        status: str,
        result: dict[str, Any] | None = None,
        result_visible: bool = True,
        error_code: str | None = None,
    ) -> None:
        """收尾台账：``succeeded`` 带结果正文，``failed`` 带失败码，``cancelled`` 表示已作废。"""
        if not request_id:
            return
        try:
            async with async_session_factory() as session, session.begin():
                await SystemRequestLogCRUD(session).finish(
                    request_id=request_id,
                    status=status,
                    result=result,
                    result_visible=result_visible,
                    error_code=error_code,
                )
        except SQLAlchemyError as exc:
            logger.error("结果台账收尾失败（该 request_id 后续查询会停在 running）: request_id={} err={}", request_id, exc)

    async def lookup(self, request_id: str) -> RequestLogRecord | None:
        """按 ``request_id`` 查台账；未登记返回 None（契约层转 404）。"""
        async with async_session_factory() as session:
            row = await SystemRequestLogCRUD(session).get_by_request_id(request_id)
        if row is None:
            return None
        return RequestLogRecord(
            request_id=row.request_id,
            operation=row.operation,
            status=row.status,
            result_visible=bool(row.result_visible),
            result=row.result_ref,
            error_code=row.error_code,
            expires_at=row.expires_at,
            created_time=row.created_time,
        )
