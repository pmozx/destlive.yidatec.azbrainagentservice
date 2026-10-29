"""契约 `/v1` 接入层回归（M1）：错误码、四头签名、幂等台账、审计留痕与 20 路由骨架。

覆盖口径（对应计划 §四 M1 验收标志）：
1. **错误码可用**：状态码由码推导，`retryable` 只在真可重试时为 true，错误体形状固定；
2. **签名 / 防重放生产化**：签名原文形状、四类拒绝原因、`authorization` ≤120s、nonce 落库判重；
3. **幂等可用**：同 ID 同正文回放、异正文 409、删除类墓碑、失败可释放重试；
4. **审计扩展生效**：`/v1` 的 GET 也纳入审计，`security_check` 四字段进库；
5. **20 条路由骨架**：路径/方法与手册一致，带签名能拿到确定响应（501 骨架占位）。

DB 类用例在数据库不可达时自动跳过（单测部分不依赖外部服务）。
"""

import json
import socket
import uuid
from functools import lru_cache
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request as StarletteRequest

from app import create_app
from app.config.setting import settings
from app.contract import errors as contract_errors
from app.contract.audit import AUTH_MODE_HMAC, SIGNATURE_FAILED, SIGNATURE_PASSED, SecurityRecord
from app.contract.deps import load_json_body
from app.contract.idempotency import (
    SCOPE_EVENT,
    SCOPE_JOB,
    SCOPE_REQUEST,
    extract_biz_id,
    payload_digest,
    complete,
    purge_expired,
    release,
    reserve,
)
from app.contract.nonce import NonceStore
from app.contract.signature import (
    HEADER_CLIENT_ID,
    HEADER_NONCE,
    HEADER_SIGNATURE,
    HEADER_TIMESTAMP,
    HEADER_VERSION,
    SIGNATURE_HEADERS,
    SIGNATURE_VERSION,
    build_signed_headers,
    canonical_string,
    verify_authorization,
    verify_signature,
)
from app.core.router_class import _build_log_data, _should_record, set_audit_security, set_audit_username

SECRET = "unit-test-secret"
CLIENT_ID = "gsi-service"
NOW_MS = 1_700_000_000_000
PATH = "/v1/knowledge/search"
BODY = b'{"request_id":"req-001","tenant_id":"gsi-default","user_id":"42"}'

# 手册 §五「接口总览」的 20 条：路径 / 方法锁定，任何增删改都必须在本表显式反映
EXPECTED_ROUTES: set[tuple[str, str]] = {
    ("POST", "/v1/parse/jobs"),
    ("GET", "/v1/parse/jobs/{job_id}"),
    ("POST", "/v1/knowledge/entries"),
    ("POST", "/v1/knowledge/entries/{publication_id}/query"),
    ("PUT", "/v1/knowledge/entries/{publication_id}"),
    ("DELETE", "/v1/knowledge/entries/{publication_id}"),
    ("POST", "/v1/knowledge/search"),
    ("POST", "/v1/chat/sessions"),
    ("POST", "/v1/chat/sessions/query"),
    ("POST", "/v1/chat/sessions/{session_id}/query"),
    ("PATCH", "/v1/chat/sessions/{session_id}"),
    ("DELETE", "/v1/chat/sessions/{session_id}"),
    ("DELETE", "/v1/chat/sessions/{session_id}/messages"),
    ("POST", "/v1/chat/completions"),
    ("POST", "/v1/chat/requests/{target_request_id}/query"),
    ("POST", "/v1/knowledge/duplicates/check"),
    ("POST", "/v1/knowledge/duplicates/groups/query"),
    ("POST", "/v1/knowledge/duplicates/groups/{group_id}/query"),
    ("POST", "/v1/knowledge/duplicates/merge-preview"),
    ("POST", "/v1/knowledge/entries/{publication_id}/deactivate"),
}


@lru_cache(maxsize=1)
def _db_reachable() -> bool:
    """台账与冒烟用例依赖真实 PostgreSQL（JSONB + ON CONFLICT）：不可达就跳过，不让本地无库时红一片。"""
    try:
        with socket.create_connection((settings.DATABASE_HOST, settings.DATABASE_PORT), timeout=2):
            return True
    except OSError:
        return False


requires_db = pytest.mark.skipif(not _db_reachable(), reason="数据库不可达，跳过台账 / 冒烟用例")


def _request(*, method: str = "POST", path: str = PATH, body: bytes = b"") -> StarletteRequest:
    """构造不经过 HTTP 的 Request（审计组装单测用）：receive 只投递一次正文。"""
    delivered = False

    async def receive() -> dict[str, Any]:
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"content-type", b"application/json"), (b"user-agent", b"pytest")],
        "client": ("127.0.0.1", 54321),
        "server": ("testserver", 80),
        "state": {},
    }
    return StarletteRequest(scope, receive)


# ── 错误码全量表（T1.2） ──────────────────────────────────────────────


def test_error_code_table_covers_every_status_class() -> None:
    """码表覆盖契约用到的全部状态类别，且每个码都有固定话术（避免回显内部细节）。"""
    assert {400, 401, 403, 404, 409, 413, 429, 500, 503} <= set(contract_errors.ERROR_CODE_STATUS.values())
    for code in contract_errors.ERROR_CODE_STATUS:
        assert code in contract_errors.DEFAULT_MESSAGES, f"{code} 缺少固定话术"
        assert code == code.upper(), f"{code} 必须与手册一致（大写下划线）"


def test_contract_error_derives_status_and_retryable() -> None:
    """状态码与 retryable 由码推导：4xx 一律不可重试，429/503/5xx 才可重试。"""
    cases = [
        (contract_errors.INVALID_REQUEST, 400, False),
        (contract_errors.SCOPE_REQUIRED, 400, False),
        (contract_errors.UNAUTHORIZED, 401, False),
        (contract_errors.CONTEXT_OUT_OF_SCOPE, 403, False),
        (contract_errors.NOT_FOUND, 404, False),
        (contract_errors.IDEMPOTENCY_CONFLICT, 409, False),
        (contract_errors.STALE_REVISION, 409, False),
        (contract_errors.SOURCE_CHANGED, 409, False),
        (contract_errors.RESULT_EXPIRED, 409, False),
        (contract_errors.PAYLOAD_TOO_LARGE, 413, False),
        (contract_errors.RATE_LIMITED, 429, True),
        (contract_errors.AUTHORIZATION_UNAVAILABLE, 503, True),
        (contract_errors.INTERNAL_ERROR, 500, True),
    ]
    for code, status_code, retryable in cases:
        error = contract_errors.ContractApiError(code)
        assert (error.status_code, error.retryable) == (status_code, retryable), code


def test_error_body_omits_request_id_when_unknown() -> None:
    """错误体固定三段式；识别不到请求时省略 request_id（手册允许省略）。"""
    body = contract_errors.error_body(contract_errors.NOT_FOUND, "资源不存在。")
    assert body == {"error": {"code": "NOT_FOUND", "message": "资源不存在。", "retryable": False}}
    assert contract_errors.error_body("NOT_FOUND", "x", request_id="req-1")["request_id"] == "req-1"


# ── 四头签名（T1.1） ─────────────────────────────────────────────────


def _headers(*, timestamp_ms: int = NOW_MS, nonce: str = "unit-test-nonce-0001") -> dict[str, str]:
    return build_signed_headers(
        SECRET, client_id=CLIENT_ID, method="POST", path=PATH, body=BODY, timestamp_ms=timestamp_ms, nonce=nonce
    )


def _verify(headers: dict[str, str], *, body: bytes = BODY, current_ms: int = NOW_MS):
    return verify_signature(
        headers,
        method="POST",
        path=PATH,
        query="",
        body=body,
        secret=SECRET,
        client_id=CLIENT_ID,
        current_ms=current_ms,
    )


def test_canonical_string_is_seven_lines_without_trailing_newline() -> None:
    """签名原文固定 7 行、LF 分隔、末尾无换行，方法大写，末行为正文 SHA-256。"""
    canonical = canonical_string(
        client_id=CLIENT_ID, timestamp=str(NOW_MS), nonce="unit-test-nonce-0001", method="post", target=PATH, body=BODY
    )
    lines = canonical.split("\n")
    assert len(lines) == 7
    assert lines[0] == SIGNATURE_VERSION
    assert lines[4] == "POST"
    assert not canonical.endswith("\n")


def test_signed_request_roundtrip_returns_digest_only() -> None:
    """自签自验通过；返回的签名摘要只保留前 8 + 后 4（审计不存原文）。"""
    headers = _headers()
    material = _verify(headers)
    assert material.client_id == CLIENT_ID
    assert material.timestamp_ms == NOW_MS
    assert len(material.signature_digest) == 12
    assert material.signature_digest[8:] == headers[HEADER_SIGNATURE][-4:]


@pytest.mark.parametrize(
    ("case", "headers", "body"),
    [
        ("缺头", {k: v for k, v in _headers().items() if k != HEADER_NONCE}, BODY),
        ("版本不符", {**_headers(), HEADER_VERSION: "v2"}, BODY),
        ("客户端不符", {**_headers(), HEADER_CLIENT_ID: "other-client"}, BODY),
        ("时间戳格式", {**_headers(), HEADER_TIMESTAMP: "1700000000"}, BODY),
        ("nonce 格式", {**_headers(), HEADER_NONCE: "short"}, BODY),
        ("签名不符", {**_headers(), HEADER_SIGNATURE: "0" * 64}, BODY),
        ("正文被改", _headers(), b'{"request_id":"req-999"}'),
    ],
)
def test_signature_failures_are_401_unauthorized(case: str, headers: dict[str, str], body: bytes) -> None:
    """七类失败原因统一 401 `UNAUTHORIZED`，不区分「头缺失」与「签名不符」的细节。"""
    with pytest.raises(contract_errors.ContractApiError) as exc:
        _verify(headers, body=body)
    assert (exc.value.code, exc.value.status_code) == (contract_errors.UNAUTHORIZED, 401), case


def test_timestamp_out_of_window_is_rejected() -> None:
    """时间戳早于窗口边界 1ms 即拒绝（±5 分钟口径）。"""
    with pytest.raises(contract_errors.ContractApiError) as exc:
        _verify(_headers(timestamp_ms=NOW_MS - settings.CONTRACT_CLOCK_SKEW_MS - 1))
    assert exc.value.code == contract_errors.UNAUTHORIZED


def test_authorization_block_rules() -> None:
    """`authorization`：缺失 400 SCOPE_REQUIRED、跨度超限 400、已过期 401、合法通过、可选块 null 跳过。"""
    verify_authorization(None)
    verify_authorization({"scope_id": "scope-001", "issued_at": NOW_MS, "expires_at": NOW_MS + 60_000}, current_ms=NOW_MS)

    with pytest.raises(contract_errors.ContractApiError) as exc:
        verify_authorization({"issued_at": NOW_MS, "expires_at": NOW_MS + 1_000}, current_ms=NOW_MS)
    assert (exc.value.code, exc.value.status_code) == (contract_errors.SCOPE_REQUIRED, 400)

    with pytest.raises(contract_errors.ContractApiError) as exc:
        verify_authorization(
            {"scope_id": "s", "issued_at": NOW_MS, "expires_at": NOW_MS + 120_001}, current_ms=NOW_MS
        )
    assert (exc.value.code, exc.value.status_code) == (contract_errors.INVALID_REQUEST, 400)

    with pytest.raises(contract_errors.ContractApiError) as exc:
        verify_authorization(
            {"scope_id": "s", "issued_at": NOW_MS - 60_000, "expires_at": NOW_MS - 1}, current_ms=NOW_MS
        )
    assert (exc.value.code, exc.value.status_code) == (contract_errors.UNAUTHORIZED, 401)


# ── 幂等口径（T1.3） ─────────────────────────────────────────────────


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


# ── OpenAPI 口径（Swagger 上能看懂怎么调） ───────────────────────────


def test_twenty_contract_routes_are_registered() -> None:
    """20 条路由的路径与方法与手册 §五 完全一致（POC 已被契约层接管，不再重复挂载）。"""
    schema = create_app().openapi()
    actual = {(method.upper(), path) for path, operations in schema["paths"].items() if path.startswith("/v1/") for method in operations}
    assert actual == EXPECTED_ROUTES
    assert len(actual) == 20


def test_signature_headers_are_documented_as_optional() -> None:
    """五个签名头都在 OpenAPI 参数里：header、非必填、带说明。

    误标必填会让 FastAPI 先抛参数校验错误，缺头就从契约的 401 变成 400。
    """
    operation = create_app().openapi()["paths"]["/v1/knowledge/search"]["post"]
    parameters = {item["name"]: item for item in operation["parameters"]}
    for header in SIGNATURE_HEADERS:
        assert parameters[header]["in"] == "header"
        assert parameters[header]["required"] is False
        assert parameters[header]["description"]


def test_signature_parameter_explains_the_canonical_string() -> None:
    """`X-AI-Signature` 的说明必须写清算法、行数、换行符与摘要口径。"""
    operation = create_app().openapi()["paths"]["/v1/knowledge/search"]["post"]
    description = {item["name"]: item for item in operation["parameters"]}["X-AI-Signature"]["description"]
    for fragment in ("HMAC-SHA256", "7 行", "LF", "SHA-256"):
        assert fragment in description


def test_error_responses_point_to_contract_error_body() -> None:
    """400/401/422/501 的响应都指向契约错误体，422 的说明要交代「已归一成 400」。"""
    operation = create_app().openapi()["paths"]["/v1/knowledge/search"]["post"]
    responses = operation["responses"]
    assert {"400", "401", "403", "404", "409", "413", "429", "501", "503"} <= set(responses)
    for code in ("400", "401", "409", "501"):
        ref = responses[code]["content"]["application/json"]["schema"]["$ref"]
        assert "ContractErrorResponse" in ref, code
    assert "400" in responses["422"]["description"]


# ── 审计扩展（T1.4） ─────────────────────────────────────────────────


def test_should_record_covers_contract_get() -> None:
    """`/v1` 的 GET（#02 轮询）也必须留痕；`/api/v1` 的查询仍按方法白名单过滤。"""
    assert _should_record(_request(method="GET", path="/v1/parse/jobs/job-1")) is True
    assert _should_record(_request(method="POST", path="/v1/parse/jobs")) is True
    assert _should_record(_request(method="GET", path="/api/v1/system/user/info")) is False
    assert _should_record(_request(method="GET", path="/docs")) is False


async def test_build_log_data_carries_security_check() -> None:
    """审计记录带上 auth_mode / client_id / request_id / security_check 四字段与操作人。"""
    request = _request(body=BODY)
    record = SecurityRecord(headers_present=list(SIGNATURE_HEADERS), signature_result=SIGNATURE_PASSED)
    record.nonce_state = "first_use"
    record.signature_digest = "9d1c0f4a3b1a9f"
    set_audit_security(
        request,
        auth_mode=AUTH_MODE_HMAC,
        client_id=CLIENT_ID,
        request_id="req-001",
        security_check=record.as_dict(),
    )
    set_audit_username(request, "user:42")

    log_data = await _build_log_data(
        request, status=0, response_code=501, description="#07 知识检索", response_json=None, start=0.0
    )
    assert log_data["auth_mode"] == AUTH_MODE_HMAC
    assert log_data["client_id"] == CLIENT_ID
    assert log_data["request_id"] == "req-001"
    assert log_data["username"] == "user:42"
    assert log_data["security_check"]["signature_result"] == SIGNATURE_PASSED
    assert log_data["security_check"]["nonce_state"] == "first_use"


@requires_db
async def test_api_log_persists_security_check_jsonb() -> None:
    """`security_check` 能落 JSONB 并原样读回（被拒请求的留痕就靠这一列）。"""
    from sqlalchemy import delete, select

    from app.core.database import async_session_factory
    from app.core.router_class import _write_api_log_async
    from app.modules.system.api_log.model import SystemApiLogModel

    marker = f"audit-test-{uuid.uuid4().hex[:12]}"
    record = SecurityRecord(headers_present=list(SIGNATURE_HEADERS), signature_result=SIGNATURE_FAILED)
    record.failure_code = contract_errors.UNAUTHORIZED
    await _write_api_log_async(
        {
            "username": "client:gsi-service",
            "status": 1,
            "description": "#01 | 签名校验失败",
            "request_path": f"/v1/{marker}",
            "request_method": "POST",
            "request_query": None,
            "request_payload": None,
            "response_code": 401,
            "response_json": None,
            "process_time": "1.0ms",
            "request_ip": "127.0.0.1",
            "user_agent": "pytest",
            "auth_mode": AUTH_MODE_HMAC,
            "client_id": CLIENT_ID,
            "request_id": marker,
            "security_check": record.as_dict(),
        }
    )

    async with async_session_factory() as session:
        row = (
            await session.execute(select(SystemApiLogModel).where(SystemApiLogModel.request_id == marker))
        ).scalar_one()
        assert row.security_check["failure_code"] == contract_errors.UNAUTHORIZED
        assert row.security_check["nonce_state"] == "not_checked"
        assert row.client_id == CLIENT_ID
        assert row.status == 1
        await session.execute(delete(SystemApiLogModel).where(SystemApiLogModel.request_id == marker))
        await session.commit()


# ── nonce 台账（T1.1） ───────────────────────────────────────────────


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


# ── 幂等台账（T1.3） ─────────────────────────────────────────────────


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


# ── HTTP 冒烟：签名打通的 20 路由骨架（T1.5） ────────────────────────


async def _call(method: str, path: str, *, body: bytes = b"", headers: dict[str, str] | None = None):
    """在进程内调用应用（ASGITransport 不走网络，与用例同一个事件循环，连接池也能复用）。"""
    request_headers = {"Content-Type": "application/json"} if body else {}
    request_headers.update(headers or {})
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
        return await client.request(method, path, content=body, headers=request_headers)


@requires_db
async def test_unsigned_request_is_rejected_with_contract_body() -> None:
    """不带签名头：401 + 契约错误体（不是 400 参数校验，也不回显期望值）。"""
    response = await _call("GET", "/v1/parse/jobs/job-1")
    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == contract_errors.UNAUTHORIZED
    assert body["error"]["retryable"] is False
    assert response.headers["WWW-Authenticate"].startswith("HMAC")


@requires_db
async def test_missing_shared_secret_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """密钥留空必须 503：放行等于把 HMAC 鉴权关掉（test/uat/prod 的密钥待填就是这个状态）。"""
    monkeypatch.setattr(settings, "CONTRACT_V1_SHARED_SECRET", "")
    headers = build_signed_headers(
        "",
        client_id=settings.CONTRACT_V1_CLIENT_ID,
        method="GET",
        path="/v1/parse/jobs/job-1",
    )
    response = await _call("GET", "/v1/parse/jobs/job-1", headers=headers)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == contract_errors.AUTHORIZATION_UNAVAILABLE


@requires_db
async def test_signed_skeleton_returns_501_placeholder() -> None:
    """带签名打通 20 路由骨架：#07 返回 501 骨架占位，错误体与 request_id 回带齐全。"""
    headers = build_signed_headers(
        settings.CONTRACT_V1_SHARED_SECRET,
        client_id=settings.CONTRACT_V1_CLIENT_ID,
        method="POST",
        path="/v1/knowledge/search",
        body=BODY,
    )
    response = await _call("POST", "/v1/knowledge/search", body=BODY, headers=headers)
    assert response.status_code == 501
    body = response.json()
    assert body["error"]["code"] == contract_errors.INTERNAL_ERROR
    assert body["error"]["retryable"] is False
    assert body["request_id"] == "req-001"


@requires_db
async def test_replayed_nonce_is_rejected() -> None:
    """同一 nonce 二次到达：401 `UNAUTHORIZED`（防重放落库生效，跨进程重启仍有效）。"""
    headers = build_signed_headers(
        settings.CONTRACT_V1_SHARED_SECRET,
        client_id=settings.CONTRACT_V1_CLIENT_ID,
        method="GET",
        path="/v1/parse/jobs/job-1",
        nonce=f"replay-{uuid.uuid4().hex}",
    )
    first = await _call("GET", "/v1/parse/jobs/job-1", headers=headers)
    assert first.status_code == 501

    second = await _call("GET", "/v1/parse/jobs/job-1", headers=headers)
    assert second.status_code == 401
    assert second.json()["error"]["code"] == contract_errors.UNAUTHORIZED


@requires_db
async def test_authorization_window_is_enforced_over_http() -> None:
    """请求正文的 authorization 跨度超限 → 400 `INVALID_REQUEST`；过期 → 401。"""
    over_ttl = (
        b'{"request_id":"req-ttl","tenant_id":"gsi-default","user_id":"42",'
        b'"authorization":{"scope_id":"s1","issued_at":1,"expires_at":600001}}'
    )
    headers = build_signed_headers(
        settings.CONTRACT_V1_SHARED_SECRET,
        client_id=settings.CONTRACT_V1_CLIENT_ID,
        method="POST",
        path="/v1/knowledge/search",
        body=over_ttl,
    )
    response = await _call("POST", "/v1/knowledge/search", body=over_ttl, headers=headers)
    assert response.status_code == 400
    body_json = response.json()
    assert body_json["error"]["code"] == contract_errors.INVALID_REQUEST
    # 必须是时效超限的具体话术，不能是参数校验兜底（否则说明请求根本没走到 authorization 校验）
    assert "120" in body_json["error"]["message"]
    # 验签阶段看不到正文的异常类型，出口必须把 request_id 从原始正文里补回来
    assert body_json["request_id"] == "req-ttl"


def test_failed_response_echoes_request_id_and_marks_failure_code() -> None:
    """签名通过后才失败的请求：错误体回带 request_id，且 `security_check` 补记失败码。

    两件事都不在验签依赖里做：漏了就变成「4xx 但审计里只看到 signature_result=passed」，
    取证时既定位不到请求，也分不清是谁拒的。
    """
    request = _request(body=BODY)
    record = SecurityRecord(headers_present=list(SIGNATURE_HEADERS), signature_result=SIGNATURE_PASSED)
    set_audit_security(
        request,
        auth_mode=AUTH_MODE_HMAC,
        client_id=CLIENT_ID,
        request_id="req-001",
        security_check=record.as_dict(),
    )

    response = contract_errors._failed(
        request,
        contract_errors.ContractApiError(contract_errors.INVALID_REQUEST),
        BODY,
    )

    assert json.loads(response.body)["request_id"] == "req-001"
    assert request.state.audit_security_check["failure_code"] == contract_errors.INVALID_REQUEST
    # 已知的验签结论不能被出口补记覆盖
    assert request.state.audit_security_check["signature_result"] == SIGNATURE_PASSED
