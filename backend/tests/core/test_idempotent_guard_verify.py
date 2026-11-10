"""幂等依赖的业务存活校验（``deps._reserve_with_verify``）回归测试。

背景：幂等台账保留 90 天，业务行却可能被清库/迁库带走。此前 #01 会一直回放首次 202，
而 #02 永远 404；这里钉住「校验不通过就作废台账、按首次请求重新受理」。
"""

from app.modules_api.base import deps
from app.modules_api.base.idempotency import Reservation

_BIZ_ID = "parse-doc-100001"
_DIGEST = "digest-for-test"


def _replay() -> Reservation:
    return Reservation(
        scope="job",
        biz_id=_BIZ_ID,
        body_hash=_DIGEST,
        state="replay",
        result={"status_code": 202, "body": {"status": "queued"}},
    )


def _new() -> Reservation:
    return Reservation(scope="job", biz_id=_BIZ_ID, body_hash=_DIGEST, state="new")


def _patch(monkeypatch, reservations: list[Reservation]) -> list[str]:
    """按顺序返回预约结果，并记录 reserve / release 的调用轨迹。"""
    events: list[str] = []
    pending = list(reservations)

    async def fake_reserve(**_kwargs: object) -> Reservation:
        events.append("reserve")
        return pending.pop(0)

    async def fake_release(**_kwargs: object) -> None:
        events.append("release")

    monkeypatch.setattr(deps, "reserve", fake_reserve)
    monkeypatch.setattr(deps, "release", fake_release)
    return events


async def test_replay_kept_when_business_row_alive(monkeypatch) -> None:
    """业务行还在：照旧回放首次结果，不碰台账。"""
    events = _patch(monkeypatch, [_replay()])

    async def alive(_biz_id: str) -> bool:
        return True

    reservation = await deps._reserve_with_verify(
        scope="job", biz_id=_BIZ_ID, digest=_DIGEST, verify_existing=alive
    )
    assert reservation.is_replay
    assert events == ["reserve"]


async def test_replay_dropped_when_business_row_missing(monkeypatch) -> None:
    """业务行没了：先释放台账，再按首次请求重新预约。"""
    events = _patch(monkeypatch, [_replay(), _new()])

    async def missing(_biz_id: str) -> bool:
        return False

    reservation = await deps._reserve_with_verify(
        scope="job", biz_id=_BIZ_ID, digest=_DIGEST, verify_existing=missing
    )
    assert not reservation.is_replay
    assert reservation.state == "new"
    assert events == ["reserve", "release", "reserve"]


async def test_no_verify_keeps_legacy_behaviour(monkeypatch) -> None:
    """未传校验的业务域（request / event）保持原语义：直接回放。"""
    events = _patch(monkeypatch, [_replay()])
    reservation = await deps._reserve_with_verify(
        scope="job", biz_id=_BIZ_ID, digest=_DIGEST, verify_existing=None
    )
    assert reservation.is_replay
    assert events == ["reserve"]


# ── 幂等摘要口径：授权块的时间戳不参与判定 ──────────────────────────

_AUTH_PAYLOAD = {
    "request_id": "req-1",
    "question": "造林对流量历时曲线有哪些影响？",
    "authorization": {"scope_id": "scope-1", "issued_at": 1, "expires_at": 2},
}


def test_idempotency_payload_ignores_authorization_clock() -> None:
    """重试时重新签发的 issued_at / expires_at 不算换正文。"""
    retried = {
        **_AUTH_PAYLOAD,
        "authorization": {"scope_id": "scope-1", "issued_at": 9_999, "expires_at": 10_000},
    }
    assert deps.idempotency_payload(_AUTH_PAYLOAD) == deps.idempotency_payload(retried)


def test_idempotency_payload_keeps_scope_and_body() -> None:
    """scope_id 与业务字段仍参与判定：换授权范围 / 换正文必须判成不同。"""
    other_scope = {
        **_AUTH_PAYLOAD,
        "authorization": {"scope_id": "scope-2", "issued_at": 1, "expires_at": 2},
    }
    other_body = {**_AUTH_PAYLOAD, "question": "另一个问题"}
    assert deps.idempotency_payload(_AUTH_PAYLOAD) != deps.idempotency_payload(other_scope)
    assert deps.idempotency_payload(_AUTH_PAYLOAD) != deps.idempotency_payload(other_body)


def test_idempotency_payload_passes_through_non_dict() -> None:
    """非对象正文原样返回，不因剔除授权字段而崩。"""
    assert deps.idempotency_payload(["a"]) == ["a"]
    assert deps.idempotency_payload(None) is None

