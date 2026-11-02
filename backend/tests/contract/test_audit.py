"""审计留痕：哪些请求要记、记什么、`security_check` 怎么落库。

审计是「被拒请求也要能查」的唯一手段，所以三件事都要钉住：
`/v1` 的 GET 必须留痕（轮询接口最容易被漏掉）、组装出的记录带全安全四字段、
`security_check` 能作为 JSONB 原样进出（失败原因只在这一列里）。
"""

import uuid

from app.core.router_class import _build_log_data, _should_record, set_audit_security, set_audit_username
from app.core.security_signature import SIGNATURE_HEADERS
from app.modules_api.contract import errors as contract_errors
from app.modules_api.contract.audit import AUTH_MODE_HMAC, SIGNATURE_FAILED, SIGNATURE_PASSED, SecurityRecord

from tests.support import BODY, CLIENT_ID, make_request, requires_db


def test_should_record_covers_contract_get() -> None:
    """`/v1` 的 GET（#02 轮询）也必须留痕；`/api/v1` 的查询仍按方法白名单过滤。"""
    assert _should_record(make_request(method="GET", path="/v1/parse/jobs/job-1")) is True
    assert _should_record(make_request(method="POST", path="/v1/parse/jobs")) is True
    assert _should_record(make_request(method="GET", path="/api/v1/system/user/info")) is False
    assert _should_record(make_request(method="GET", path="/docs")) is False


async def test_build_log_data_carries_security_check() -> None:
    """审计记录带上 auth_mode / client_id / request_id / security_check 四字段与操作人。"""
    request = make_request(body=BODY)
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
