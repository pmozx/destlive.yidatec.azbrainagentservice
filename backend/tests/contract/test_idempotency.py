"""幂等与防重放：守卫状态机、台账语义、nonce 判重。

覆盖三层：

1. **守卫状态机**（P0-2）：`_idempotent_guard` 的异常路径必须 release——客户端断连
   （GeneratorExit）、任务取消（CancelledError）、未被路由类接住的异常，三条都必须释放预约，
   否则预约永久锁在 `processing`，后续重试全部 409；
2. **台账语义**（T1.3）：同 ID 同正文回放、异正文 409、删除类墓碑永不过期、失败后可重新进入；
3. **nonce 判重**（T1.1）：首用通过、窗口内重放拒绝、过 TTL 可接管。

台账服务用 PostgreSQL 方言（pg_insert），sqlite 起不来，所以守卫部分 monkeypatch
`app.contract.deps` 的 reserve/release/complete 为记录桩；台账部分打 `@requires_db`。
"""

import asyncio
import uuid
from types import SimpleNamespace

import pytest

from app.contract import deps as contract_deps
from app.contract import errors as contract_errors
from app.contract.deps import _idempotent_guard, load_json_body
from app.contract.idempotency import (
    SCOPE_EVENT,
    SCOPE_JOB,
    SCOPE_REQUEST,
    Reservation,
    complete,
    extract_biz_id,
    payload_digest,
    purge_expired,
    release,
    reserve,
)
from app.contract.nonce import NonceStore

from tests.support import CLIENT_ID, NOW_MS, requires_db

STATE_NEW = "new"
STATE_REPLAY = "replay"


@pytest.fixture()
def calls(monkeypatch: pytest.MonkeyPatch) -> dict[str, list]:
    """把契约层幂等 API 换成记录桩：reserve 恒返回 new，release/complete 记录调用。"""
    record: dict[str, list] = {"release": [], "complete": []}
    counter = {"n": 0}

    async def fake_reserve(*, scope: str, biz_id: str, body_hash: str, **_kw) -> Reservation:
        counter["n"] += 1
        return Reservation(scope=scope, biz_id=biz_id, body_hash=body_hash, state=STATE_NEW)

    async def fake_release(*, scope: str, biz_id: str, body_hash: str) -> None:
        record["release"].append((scope, biz_id, body_hash))

    async def fake_complete(*, scope: str, biz_id: str, body_hash: str, result=None, **_kw) -> None:
        record["complete"].append((scope, biz_id, body_hash, result))

    monkeypatch.setattr(contract_deps, "reserve", fake_reserve)
    monkeypatch.setattr(contract_deps, "release", fake_release)
    monkeypatch.setattr(contract_deps, "complete", fake_complete)
    return record


def _make_request(payload: bytes = b'{"request_id": "req-1"}') -> SimpleNamespace:
    """守卫只用到 request.body() 和 request.state，duck-typing 即可。"""
    return SimpleNamespace(body=lambda: _async_return(payload), state=SimpleNamespace())


async def _async_return(data: bytes) -> bytes:
    return data


async def _drive_guard(request, *, settle: str | None = None):
    """驱动守卫依赖到 yield 点，返回生成器供调用方决定收尾方式。"""
    gen = _idempotent_guard(SCOPE_REQUEST)(request)
    reservation = await gen.__anext__()
    if settle == "result":
        request.state.idempotency_result = {"status_code": 200, "body": {"ok": True}}
    elif settle == "failed":
        request.state.contract_failed = True
    return gen, reservation


async def test_disconnect_releases_reservation(calls, monkeypatch):
    """客户端断连：生成器被 aclose()（GeneratorExit 抛进 yield）→ 必须释放预约。"""
    request = _make_request()
    monkeypatch.setattr(
        contract_deps, "payload_digest", lambda _p: "hash-disconnect"
    )
    gen, reservation = await _drive_guard(request)

    await gen.aclose()  # 模拟断连：Starlette 关闭依赖生成器

    assert calls["release"] == [(SCOPE_REQUEST, "req-1", "hash-disconnect")]
    assert calls["complete"] == []
    assert reservation.state == STATE_NEW


async def test_cancellation_releases_reservation(calls, monkeypatch):
    """请求任务被取消：CancelledError 抛进生成器 → 必须释放预约且异常继续上抛。"""
    request = _make_request()
    monkeypatch.setattr(contract_deps, "payload_digest", lambda _p: "hash-cancel")
    gen, _reservation = await _drive_guard(request)

    with pytest.raises(asyncio.CancelledError):
        await gen.athrow(asyncio.CancelledError)

    assert calls["release"] == [(SCOPE_REQUEST, "req-1", "hash-cancel")]
    assert calls["complete"] == []


async def test_uncaught_exception_releases_and_reraises(calls, monkeypatch):
    """路由类没接住的异常（如响应序列化失败）抛进生成器 → 释放并原样上抛。"""
    request = _make_request()
    monkeypatch.setattr(contract_deps, "payload_digest", lambda _p: "hash-error")
    gen, _reservation = await _drive_guard(request)

    class Boom(RuntimeError):
        pass

    with pytest.raises(Boom):
        await gen.athrow(Boom("响应序列化失败"))

    assert calls["release"] == [(SCOPE_REQUEST, "req-1", "hash-error")]
    assert calls["complete"] == []


async def test_normal_success_completes_not_releases(calls, monkeypatch):
    """成功路径：路由调用了 record_receipt（settled）→ complete 兜底不重复、不释放。"""
    request = _make_request()
    monkeypatch.setattr(contract_deps, "payload_digest", lambda _p: "hash-ok")
    gen, _reservation = await _drive_guard(request, settle="result")
    request.state.idempotency_settled = True  # record_receipt 的行为

    with pytest.raises(StopAsyncIteration):
        await gen.asend(None)  # 正常走完

    assert calls["release"] == []
    assert calls["complete"] == []  # 已 settled，兜底不重复登记


async def test_normal_failure_still_releases(calls, monkeypatch):
    """出口 1 回归保护：正常退出 + contract_failed 标记 → 释放（原修复行为不变）。"""
    request = _make_request()
    monkeypatch.setattr(contract_deps, "payload_digest", lambda _p: "hash-failed")
    gen, _reservation = await _drive_guard(request, settle="failed")

    with pytest.raises(StopAsyncIteration):
        await gen.asend(None)

    assert calls["release"] == [(SCOPE_REQUEST, "req-1", "hash-failed")]
    assert calls["complete"] == []


async def test_replay_never_releases(calls, monkeypatch):
    """回放请求：is_replay=True → 提前返回，绝不释放首次记录。"""
    record_calls = calls  # noqa: F841

    async def fake_reserve_replay(*, scope, biz_id, body_hash, **_kw):
        return Reservation(scope=scope, biz_id=biz_id, body_hash=body_hash, state=STATE_REPLAY)

    import app.contract.deps as d

    original = d.reserve
    d.reserve = fake_reserve_replay
    try:
        request = _make_request()
        gen = _idempotent_guard(SCOPE_REQUEST)(request)
        _reservation = await gen.__anext__()
        with pytest.raises(StopAsyncIteration):
            await gen.asend(None)
    finally:
        d.reserve = original

    assert calls["release"] == []
    assert calls["complete"] == []


# ── 纯函数口径（T1.3）：正文摘要与业务键提取 ────────────────────────


def test_payload_digest_is_key_order_insensitive() -> None:
    """键序不同但语义相同的正文必须同 hash，否则正常重试会被误判成「异正文」409。"""
    first = load_json_body(b'{"a":1,"b":{"c":2}}')
    second = load_json_body(b'{"b":{"c":2},"a":1}')
    assert payload_digest(first) == payload_digest(second)
    assert payload_digest({"a": 1}) != payload_digest({"a": 2})


def test_extract_biz_id_per_scope() -> None:
    """三类幂等域各认自己的键；event 域允许退化为 request_id。"""
    assert extract_biz_id(SCOPE_JOB, {"job_id": "job-1", "request_id": "req-1"}) == "job-1"
    assert extract_biz_id(SCOPE_REQUEST, {"request_id": "req-1"}) == "req-1"
    assert extract_biz_id(SCOPE_EVENT, {"request_id": "req-1"}) == "req-1"
    assert extract_biz_id(SCOPE_EVENT, {"event_id": "evt-1", "request_id": "req-1"}) == "evt-1"
    assert extract_biz_id(SCOPE_JOB, {"request_id": "req-1"}) is None


# ── nonce 台账（T1.1） ──────────────────────────────────────────────


@requires_db
async def test_nonce_first_use_then_replay() -> None:
    """首用 True，窗口内第二次 False（重放）；过 TTL 后可被接管，不会永久占位。"""
    store = NonceStore(ttl_seconds=60)
    nonce = f"nonce-{uuid.uuid4().hex}"

    assert await store.register(client_id=CLIENT_ID, nonce=nonce, request_id="req-1", current_ms=NOW_MS) is True
    assert await store.register(client_id=CLIENT_ID, nonce=nonce, request_id="req-1", current_ms=NOW_MS) is False
    # 越过 TTL：旧行视为过期，允许同一个 nonce 重新进入（时间窗早已移出）
    later = NOW_MS + 61_000
    assert await store.register(client_id=CLIENT_ID, nonce=nonce, request_id="req-2", current_ms=later) is True


# ── 幂等台账（T1.3） ────────────────────────────────────────────────


@requires_db
async def test_idempotency_replay_conflict_and_tombstone() -> None:
    """同 ID 同正文回放首次结果、异正文 409、墓碑永不过期、释放后可重试。"""
    from sqlalchemy import delete, select

    from app.core.database import async_session_factory
    from app.modules.system.models import SystemIdempotencyModel

    biz_id = f"idem-{uuid.uuid4().hex[:12]}"
    digest = payload_digest({"request_id": biz_id, "v": 1})

    first = await reserve(scope=SCOPE_REQUEST, biz_id=biz_id, body_hash=digest)
    assert first.state == "new"
    await complete(scope=SCOPE_REQUEST, biz_id=biz_id, body_hash=digest, result={"status_code": 201, "body": {"ok": 1}})

    replay = await reserve(scope=SCOPE_REQUEST, biz_id=biz_id, body_hash=digest)
    assert replay.is_replay
    assert replay.result == {"status_code": 201, "body": {"ok": 1}}

    with pytest.raises(contract_errors.ContractApiError) as exc:
        await reserve(scope=SCOPE_REQUEST, biz_id=biz_id, body_hash=payload_digest({"request_id": biz_id, "v": 2}))
    assert (exc.value.code, exc.value.status_code) == (contract_errors.IDEMPOTENCY_CONFLICT, 409)

    # 墓碑：删除类记录 expires_at 为 NULL，永不被清理
    tomb = f"tomb-{uuid.uuid4().hex[:12]}"
    await reserve(scope=SCOPE_REQUEST, biz_id=tomb, body_hash=payload_digest({"request_id": tomb}))
    await complete(scope=SCOPE_REQUEST, biz_id=tomb, body_hash=payload_digest({"request_id": tomb}), tombstone=True)
    async with async_session_factory() as session:
        row = (
            await session.execute(select(SystemIdempotencyModel).where(SystemIdempotencyModel.biz_id == tomb))
        ).scalar_one()
        assert row.status == "tombstone"
        assert row.expires_at is None
        await session.execute(
            delete(SystemIdempotencyModel).where(SystemIdempotencyModel.biz_id.in_([biz_id, tomb]))
        )
        await session.commit()

    # 失败释放：同一 ID 可重新进入处理
    retry_id = f"retry-{uuid.uuid4().hex[:12]}"
    retry_hash = payload_digest({"request_id": retry_id})
    assert (await reserve(scope=SCOPE_EVENT, biz_id=retry_id, body_hash=retry_hash)).state == "new"
    await release(scope=SCOPE_EVENT, biz_id=retry_id, body_hash=retry_hash)
    assert (await reserve(scope=SCOPE_EVENT, biz_id=retry_id, body_hash=retry_hash)).state == "new"
    await release(scope=SCOPE_EVENT, biz_id=retry_id, body_hash=retry_hash)
    await purge_expired()
