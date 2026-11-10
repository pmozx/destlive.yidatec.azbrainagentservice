"""三类幂等台账（T1.3）：`job_id` / `request_id` / `event_id`，同 ID 异正文 409。

契约口径（手册 §一「幂等」、计划 §七.6、C3）：

1. **同 ID + 完全相同正文 → 幂等返回首次回执**（`state=replay`，调用方不需要重新处理）；
2. **同 ID + 不同正文 → 409 `IDEMPOTENCY_CONFLICT`**（正文变了必须换 ID，不允许悄悄覆盖）；
3. **删除类保留幂等墓碑**（`status=tombstone`、`expires_at=NULL` 永不清理），
   否则「删除 → 重发同一 request_id」会让知识复活；
4. 台账与业务同库不同事务：登记/完成都用独立会话立即提交，**业务失败必须显式 release**，
   否则一次失败会把该 ID 永久锁在 `processing`（重试全变 409）。

正文摘要用**键序无关**的规范化 JSON（``app.utils.common_util.canonical_json_sha256``）：
契约要求「同正文」，而同一份 JSON 因键序不同被判定成「不同正文」会把正常重试逼成 409。
（签名原文用**原始字节**摘要，两者口径不同，各自都对：签名防篡改，幂等判语义同一。）

摘要前剔除 ``authorization.issued_at / expires_at``（见 ``deps.idempotency_payload``）：
这两个值是「本次调用时刻」，重试时必重新签发，计入摘要会让合法重试必然 409。

数据访问与状态机已迁入 ``app.modules.system.idempotency.service.SystemIdempotencyService``；
本文件只保留契约层适配：业务 ID 提取、错误码映射与 ``Reservation`` 结构。
"""

from dataclasses import dataclass
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from app.modules.system.idempotency.service import (
    STATE_BODY_CONFLICT,
    STATE_PROCESSING,
    STATE_REPLAY,
    SystemIdempotencyService,
)

from .errors import IDEMPOTENCY_CONFLICT, INTERNAL_ERROR, ContractApiError

SCOPE_JOB = "job"
SCOPE_REQUEST = "request"
SCOPE_EVENT = "event"
IDEMPOTENCY_SCOPES: tuple[str, ...] = (SCOPE_JOB, SCOPE_REQUEST, SCOPE_EVENT)

# 每个幂等域在正文里认哪个键（契约 C3：job_id / event_id / request_id）
SCOPE_ID_KEYS: dict[str, str] = {SCOPE_JOB: "job_id", SCOPE_REQUEST: "request_id", SCOPE_EVENT: "event_id"}

_SERVICE = SystemIdempotencyService()


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
        return self.state == STATE_REPLAY


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
    """预约幂等键：首用返回 `new`，命中已完成记录返回 `replay`，异正文/处理中直接 409。"""
    try:
        outcome = await _SERVICE.reserve(scope=scope, biz_id=biz_id, body_hash=body_hash, ttl_days=ttl_days)
    except SQLAlchemyError as exc:
        raise ContractApiError(INTERNAL_ERROR, "幂等台账暂不可用，请稍后重试。", retryable=True) from exc

    if outcome.state == STATE_BODY_CONFLICT:
        _raise_conflict(scope, biz_id)
    if outcome.state == STATE_PROCESSING:
        _raise_conflict(scope, biz_id, "原请求仍在处理中，请稍后使用同一正文重试。", retryable=True)
    return Reservation(
        scope=scope,
        biz_id=biz_id,
        body_hash=body_hash,
        state=outcome.state,
        result=outcome.result,
    )


async def complete(
    *,
    scope: str,
    biz_id: str,
    body_hash: str,
    result: dict[str, Any] | None = None,
    tombstone: bool = False,
) -> None:
    """登记首次结果：`tombstone=true` 用于删除类（`expires_at=NULL`，永不清理）。"""
    await _SERVICE.complete(
        scope=scope,
        biz_id=biz_id,
        body_hash=body_hash,
        result=result,
        tombstone=tombstone,
    )


async def release(*, scope: str, biz_id: str, body_hash: str) -> None:
    """失败回滚：删掉预约，让同一 ID 的后续重试能重新进入处理（墓碑记录不受影响）。"""
    await _SERVICE.release(scope=scope, biz_id=biz_id, body_hash=body_hash)


async def purge_expired(*, current_ms: int | None = None) -> int:
    """物理清理过期台账（墓碑 `expires_at IS NULL` 不清），返回清理条数。"""
    return await _SERVICE.purge_expired(current_ms=current_ms)
