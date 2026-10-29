"""三类幂等台账（T1.3）：`job_id` / `request_id` / `event_id`，同 ID 异正文 409。

契约口径（手册 §一「幂等」、计划 §七.6、C3）：

1. **同 ID + 完全相同正文 → 幂等返回首次回执**（`state=replay`，调用方不需要重新处理）；
2. **同 ID + 不同正文 → 409 `IDEMPOTENCY_CONFLICT`**（正文变了必须换 ID，不允许悄悄覆盖）；
3. **删除类保留幂等墓碑**（`status=tombstone`、`expires_at=NULL` 永不清理），
   否则「删除 → 重发同一 request_id」会让知识复活；
4. 台账与业务同库不同事务：登记/完成都用独立会话立即提交，**业务失败必须显式 release**，
   否则一次失败会把该 ID 永久锁在 `processing`（重试全变 409）。

正文摘要用**键序无关**的规范化 JSON（`sort_keys` + 紧凑分隔符）求 SHA-256：
契约要求「同正文」，而同一份 JSON 因键序不同被判定成「不同正文」会把正常重试逼成 409。
（签名原文用**原始字节**摘要，两者口径不同，各自都对：签名防篡改，幂等判语义同一。）
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError

from app.config.setting import settings
from app.core.database import async_session_factory
from app.core.logger import logger
from app.modules.system.models import SystemIdempotencyModel

from .errors import IDEMPOTENCY_CONFLICT, INTERNAL_ERROR, ContractApiError

SCOPE_JOB = "job"
SCOPE_REQUEST = "request"
SCOPE_EVENT = "event"
IDEMPOTENCY_SCOPES: tuple[str, ...] = (SCOPE_JOB, SCOPE_REQUEST, SCOPE_EVENT)

# 每个幂等域在正文里认哪个键（契约 C3：job_id / event_id / request_id）
SCOPE_ID_KEYS: dict[str, str] = {SCOPE_JOB: "job_id", SCOPE_REQUEST: "request_id", SCOPE_EVENT: "event_id"}

_STATUS_PROCESSING = "processing"
_STATUS_SUCCEEDED = "succeeded"
_STATUS_TOMBSTONE = "tombstone"

_STATE_NEW = "new"
_STATE_REPLAY = "replay"

_BIZ_CONSTRAINT = "uq_sbp_ai_system_idempotency_scope_biz"


@dataclass(frozen=True)
class Reservation:
    """一次幂等预约的结果：`new` 表示本次是首次处理，`replay` 要直接回放 `result`。"""

    scope: str
    biz_id: str
    body_hash: str
    state: str
    result: dict[str, Any] | None = None

    @property
    def is_replay(self) -> bool:
        return self.state == _STATE_REPLAY


def payload_digest(payload: Any) -> str:
    """正文摘要：键序无关的规范化 JSON（中文不转义，保证跨语言可比）。"""
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def extract_biz_id(scope: str, payload: Any) -> str | None:
    """按幂等域从正文取业务 ID；`event` 域允许退化为 `request_id`（变更事件常只有请求 ID）。"""
    if not isinstance(payload, dict):
        return None
    key = SCOPE_ID_KEYS.get(scope, "request_id")
    value = payload.get(key)
    if value in (None, "") and scope == SCOPE_EVENT:
        value = payload.get("request_id")
    return str(value) if value not in (None, "") else None


def _raise_conflict(scope: str, biz_id: str, message: str | None = None, *, retryable: bool = False) -> None:
    """409：同 ID 异正文 / 原请求仍在处理中。`request_id` 由调用方按域补。"""
    raise ContractApiError(
        IDEMPOTENCY_CONFLICT,
        message or f"相同 {SCOPE_ID_KEYS.get(scope, 'request_id')}={biz_id} 已用于不同的请求正文。",
        retryable=retryable,
    )


async def reserve(
    *,
    scope: str,
    biz_id: str,
    body_hash: str,
    ttl_days: int | None = None,
) -> Reservation:
    """预约幂等键：首用返回 `new`，命中已完成记录返回 `replay`，异正文/处理中直接 409。

    并发同 ID 同正文的两个请求：先到的插入 `processing`，后到的看到 `processing` 回 409
    （`retryable=true`，调用方稍后用同一正文重试即可拿到首次结果），不会双写。
    """
    now = datetime.now(UTC)
    expires_at = now + timedelta(days=ttl_days if ttl_days is not None else settings.CONTRACT_IDEMPOTENCY_TTL_DAYS)
    try:
        async with async_session_factory() as session:
            inserted = await session.execute(
                pg_insert(SystemIdempotencyModel)
                .values(
                    scope=scope,
                    biz_id=biz_id,
                    body_hash=body_hash,
                    status=_STATUS_PROCESSING,
                    created_time=now,
                    expires_at=expires_at,
                )
                .on_conflict_do_nothing(constraint=_BIZ_CONSTRAINT)
                .returning(SystemIdempotencyModel.id)
            )
            if inserted.first() is not None:
                await session.commit()
                return Reservation(scope=scope, biz_id=biz_id, body_hash=body_hash, state=_STATE_NEW)

            row = (
                await session.execute(
                    select(SystemIdempotencyModel).where(
                        SystemIdempotencyModel.scope == scope,
                        SystemIdempotencyModel.biz_id == biz_id,
                    )
                )
            ).scalar_one_or_none()

            if row is None:
                # 极端竞态（刚被 release 掉）：按首用重试一次，不再递归
                await session.rollback()
            elif row.body_hash != body_hash:
                await session.rollback()
                _raise_conflict(scope, biz_id)
            elif row.expires_at is not None and row.expires_at < now:
                # 台账已过期：按新请求处理（Q11 建议口径），用条件更新避免并发抢占
                taken = await session.execute(
                    update(SystemIdempotencyModel)
                    .where(
                        SystemIdempotencyModel.scope == scope,
                        SystemIdempotencyModel.biz_id == biz_id,
                        SystemIdempotencyModel.expires_at.is_not(None),
                        SystemIdempotencyModel.expires_at < now,
                    )
                    .values(
                        body_hash=body_hash,
                        status=_STATUS_PROCESSING,
                        result_ref=None,
                        created_time=now,
                        expires_at=expires_at,
                    )
                    .returning(SystemIdempotencyModel.id)
                )
                if taken.first() is None:
                    await session.rollback()
                    _raise_conflict(scope, biz_id, "原请求仍在处理中，请稍后使用同一正文重试。", retryable=True)
                await session.commit()
                return Reservation(scope=scope, biz_id=biz_id, body_hash=body_hash, state=_STATE_NEW)
            elif row.status == _STATUS_PROCESSING:
                await session.rollback()
                _raise_conflict(scope, biz_id, "原请求仍在处理中，请稍后使用同一正文重试。", retryable=True)
            else:
                await session.commit()
                return Reservation(
                    scope=scope,
                    biz_id=biz_id,
                    body_hash=body_hash,
                    state=_STATE_REPLAY,
                    result=row.result_ref,
                )
    except SQLAlchemyError as exc:
        logger.error("幂等台账不可用，拒绝写入请求: scope={} biz_id={} err={}", scope, biz_id, exc)
        raise ContractApiError(INTERNAL_ERROR, "幂等台账暂不可用，请稍后重试。", retryable=True) from exc

    # row is None 的竞态分支：按新请求重新预约一次（幂等键刚被释放，重试必然首用）
    return await reserve(scope=scope, biz_id=biz_id, body_hash=body_hash, ttl_days=ttl_days)


async def complete(
    *,
    scope: str,
    biz_id: str,
    body_hash: str,
    result: dict[str, Any] | None = None,
    tombstone: bool = False,
) -> None:
    """登记首次结果：`tombstone=true` 用于删除类（`expires_at=NULL`，永不清理）。"""
    now = datetime.now(UTC)
    values: dict[str, Any] = {
        "status": _STATUS_TOMBSTONE if tombstone else _STATUS_SUCCEEDED,
        "result_ref": result,
        "expires_at": None if tombstone else now + timedelta(days=settings.CONTRACT_IDEMPOTENCY_TTL_DAYS),
    }
    try:
        async with async_session_factory() as session:
            await session.execute(
                update(SystemIdempotencyModel)
                .where(
                    SystemIdempotencyModel.scope == scope,
                    SystemIdempotencyModel.biz_id == biz_id,
                    SystemIdempotencyModel.body_hash == body_hash,
                )
                .values(**values)
            )
            await session.commit()
    except SQLAlchemyError as exc:
        # 结果登记失败不影响已成功的业务响应，但要留日志：该 ID 后续重试会拿到 409
        logger.error("幂等结果登记失败: scope={} biz_id={} err={}", scope, biz_id, exc)


async def release(*, scope: str, biz_id: str, body_hash: str) -> None:
    """失败回滚：删掉预约，让同一 ID 的后续重试能重新进入处理（墓碑记录不受影响）。"""
    try:
        async with async_session_factory() as session:
            await session.execute(
                delete(SystemIdempotencyModel).where(
                    SystemIdempotencyModel.scope == scope,
                    SystemIdempotencyModel.biz_id == biz_id,
                    SystemIdempotencyModel.body_hash == body_hash,
                    SystemIdempotencyModel.status != _STATUS_TOMBSTONE,
                )
            )
            await session.commit()
    except SQLAlchemyError as exc:
        logger.warning("幂等预约释放失败（该 ID 可能被锁在 processing）: scope={} biz_id={} err={}", scope, biz_id, exc)


async def purge_expired(*, current_ms: int | None = None) -> int:
    """物理清理过期台账（墓碑 `expires_at IS NULL` 不清），返回清理条数。"""
    now = datetime.fromtimestamp(current_ms / 1000, tz=UTC) if current_ms is not None else datetime.now(UTC)
    try:
        async with async_session_factory() as session:
            result = await session.execute(
                delete(SystemIdempotencyModel).where(
                    SystemIdempotencyModel.expires_at.is_not(None),
                    SystemIdempotencyModel.expires_at < now,
                )
            )
            await session.commit()
    except SQLAlchemyError as exc:
        logger.warning("过期幂等台账清理失败（不影响业务）: {}", exc)
        return 0
    return int(result.rowcount or 0)
