"""P0-2 回归测试：幂等守卫的异常路径必须释放预约。

`_idempotent_guard` 的失败有两条出口：
1. 正常退出 + `contract_failed` 标记 → release（原行为，防止回归）；
2. 异常被抛进依赖生成器（客户端断连 GeneratorExit / 任务取消 CancelledError /
   未被路由类接住的异常）→ 必须在 except 分支 release，否则预约永久锁在
   `processing`，后续重试全部 409。本文件重点覆盖出口 2。

台账服务本身用 PostgreSQL 方言（pg_insert），sqlite 起不来，所以这里直接
monkeypatch `app.contract.deps` 里的 reserve/release/complete 为记录桩，
只验证守卫的状态机逻辑。
"""

import asyncio
from types import SimpleNamespace

import pytest

from app.contract import deps as contract_deps
from app.contract.deps import SCOPE_REQUEST, _idempotent_guard
from app.contract.idempotency import Reservation

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
