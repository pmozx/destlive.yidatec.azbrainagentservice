"""契约路由的 HTTP 行为：鉴权失败怎么回、失败后审计怎么补。

这一层验证的是**穿过完整依赖链之后**的行为——验签依赖、幂等守卫、异常出口、审计补记
都在链条上，单测各自覆盖过一次，但"串联起来还对不对"只有这里能证明。

里程碑级的断言（20 条路由清单、骨架 501 占位）不在这里，见 ``tests/stages/``。
"""

import json
import uuid

from app.config.setting import settings
from app.contract import errors as contract_errors
from app.contract.audit import AUTH_MODE_HMAC, SIGNATURE_PASSED, SecurityRecord
from app.contract.signature import SIGNATURE_HEADERS, build_signed_headers
from app.core.router_class import set_audit_security

from tests.support import BODY, CLIENT_ID, call, make_request, requires_db


@requires_db
async def test_unsigned_request_is_rejected_with_contract_body() -> None:
    """不带签名头：401 + 契约错误体（不是 400 参数校验，也不回显期望值）。"""
    response = await call("GET", "/v1/parse/jobs/job-1")
    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == contract_errors.UNAUTHORIZED
    assert body["error"]["retryable"] is False
    assert response.headers["WWW-Authenticate"].startswith("HMAC")


@requires_db
async def test_missing_shared_secret_fails_closed(monkeypatch) -> None:
    """密钥留空必须 503：放行等于把 HMAC 鉴权关掉（test/uat/prod 的密钥待填就是这个状态）。"""
    monkeypatch.setattr(settings, "CONTRACT_V1_SHARED_SECRET", "")
    headers = build_signed_headers(
        "",
        client_id=settings.CONTRACT_V1_CLIENT_ID,
        method="GET",
        path="/v1/parse/jobs/job-1",
    )
    response = await call("GET", "/v1/parse/jobs/job-1", headers=headers)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == contract_errors.AUTHORIZATION_UNAVAILABLE


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
    response = await call("POST", "/v1/knowledge/search", body=over_ttl, headers=headers)
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
    request = make_request(body=BODY)
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
    )

    assert json.loads(response.body)["request_id"] == "req-001"
    assert request.state.audit_security_check["failure_code"] == contract_errors.INVALID_REQUEST
    # 已知的验签结论不能被出口补记覆盖
    assert request.state.audit_security_check["signature_result"] == SIGNATURE_PASSED


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
    first = await call("GET", "/v1/parse/jobs/job-1", headers=headers)
    # 验签通过后走到业务层：随机 job_id 不存在，归口为 404（关键是不被 nonce 判重拦住）
    assert first.status_code == 404
    assert first.json()["error"]["code"] == contract_errors.NOT_FOUND

    second = await call("GET", "/v1/parse/jobs/job-1", headers=headers)
    assert second.status_code == 401
    assert second.json()["error"]["code"] == contract_errors.UNAUTHORIZED
