"""kb 解析链路：#01 受理 → 后台解析 → #02 轮询结果。

解析任务跑在请求之外（自开短事务回写状态），所以这里验的是**跨请求的状态迁移**：
受理回执、幂等回放、前置校验归口（4xx 在 202 之前）、以及拉取超限必须立刻中断。
"""

import json
import uuid
from typing import Any

import pytest

from app.config.setting import settings
from app.contract import errors as contract_errors
from app.contract.mock_gsi import FileServer
from app.contract.parse import MAX_FILE_BYTES, fetch_file
from app.contract.signature import build_signed_headers

from tests.support import call, requires_db


def _parse_payload(job_id: str, file_url: str, **overrides: Any) -> bytes:
    """构造 #01 正文（字段与手册 §六.01 一致）；overrides 用于负向用例。"""
    payload: dict[str, Any] = {
        "job_id": job_id,
        "doc_id": "doc-001",
        "version_id": "v1",
        "file_url": file_url,
        "file_name": "vpn-guide.md",
        "options": {"chunk": True, "language": "zh"},
        "tenant_id": "gsi-default",
        "resource_type": "document",
        "file_size": 1024,
        "metadata": {"title": "VPN 申请操作手册"},
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False).encode()


def _signed_headers(method: str, path: str, *, body: bytes = b"") -> dict[str, str]:
    """每次单发一个新 nonce：幂等回放用例必须绕过「nonce 重放」这层拒绝。"""
    return build_signed_headers(
        settings.CONTRACT_V1_SHARED_SECRET,
        client_id=settings.CONTRACT_V1_CLIENT_ID,
        method=method,
        path=path,
        body=body,
        nonce=f"parse-{uuid.uuid4().hex}",
    )


async def _cleanup_parse_job(job_id: str) -> None:
    """清掉任务行、产物与幂等台账，避免用例互相污染（解析链路没有业务删除入口）。"""
    from sqlalchemy import delete

    from app.core.database import async_session_factory
    from app.modules.kb.parse_job.model import KbParseJobModel
    from app.modules.kb.parse_result.model import KbParseResultModel
    from app.modules.system.idempotency.model import SystemIdempotencyModel

    async with async_session_factory() as session:
        await session.execute(delete(KbParseResultModel).where(KbParseResultModel.job_id == job_id))
        await session.execute(delete(KbParseJobModel).where(KbParseJobModel.uuid == job_id))
        await session.execute(delete(SystemIdempotencyModel).where(SystemIdempotencyModel.biz_id == job_id))
        await session.commit()


@requires_db
async def test_parse_job_accepts_then_reaches_succeeded() -> None:
    """#01 202 受理 → 后台解析 → #02 拿到 succeeded 与完整切片（无向量、pages=[]）。"""
    job_id = f"parse-{uuid.uuid4().hex[:12]}"
    try:
        with FileServer() as server:
            body = _parse_payload(job_id, server.url)
            created = await call(
                "POST", "/v1/parse/jobs", body=body, headers=_signed_headers("POST", "/v1/parse/jobs", body=body)
            )
            assert created.status_code == 202
            receipt = created.json()
            assert receipt["job_id"] == job_id
            assert receipt["status"] in {"queued", "running", "succeeded"}

            detail = await call(
                "GET", f"/v1/parse/jobs/{job_id}", headers=_signed_headers("GET", f"/v1/parse/jobs/{job_id}")
            )
            assert detail.status_code == 200
            payload = detail.json()
            assert payload["status"] == "succeeded"
            assert payload["progress"] == 100
            assert payload["result"]["file_name"] == "vpn-guide.md"
            assert payload["result"]["pages"] == []
            assert payload["result"]["warnings"] == []
            assert payload["result"]["chunks"]
            first_chunk = payload["result"]["chunks"][0]
            assert first_chunk["seq"] == 1
            assert first_chunk["source_type"] == "text"
            assert first_chunk["content"]
    finally:
        await _cleanup_parse_job(job_id)


@requires_db
async def test_parse_job_replay_returns_same_receipt() -> None:
    """同 job_id + 同正文重试直接回放首次 202 回执，不重复建任务/写产物。"""
    job_id = f"parse-{uuid.uuid4().hex[:12]}"
    try:
        with FileServer() as server:
            body = _parse_payload(job_id, server.url)
            first = await call(
                "POST", "/v1/parse/jobs", body=body, headers=_signed_headers("POST", "/v1/parse/jobs", body=body)
            )
            second = await call(
                "POST", "/v1/parse/jobs", body=body, headers=_signed_headers("POST", "/v1/parse/jobs", body=body)
            )
            assert first.status_code == 202
            assert second.status_code == 202
            assert second.json() == first.json()
    finally:
        await _cleanup_parse_job(job_id)


@requires_db
async def test_parse_job_rejects_invalid_requests() -> None:
    """FAQ 条件必填 / 类型白名单 / 声明大小三类前置校验都在 202 之前归口成 4xx。"""
    faq_job = f"parse-{uuid.uuid4().hex[:12]}"
    body = _parse_payload(faq_job, "http://127.0.0.1:9/unused.md", resource_type="faq")
    response = await call(
        "POST", "/v1/parse/jobs", body=body, headers=_signed_headers("POST", "/v1/parse/jobs", body=body)
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == contract_errors.INVALID_REQUEST
    assert "prompt_text" in response.json()["error"]["message"]

    suffix_job = f"parse-{uuid.uuid4().hex[:12]}"
    body = _parse_payload(suffix_job, "http://127.0.0.1:9/unused.exe", file_name="setup.exe")
    response = await call(
        "POST", "/v1/parse/jobs", body=body, headers=_signed_headers("POST", "/v1/parse/jobs", body=body)
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == contract_errors.INVALID_REQUEST
    assert ".exe" in response.json()["error"]["message"]

    size_job = f"parse-{uuid.uuid4().hex[:12]}"
    body = _parse_payload(size_job, "http://127.0.0.1:9/unused.md", file_size=MAX_FILE_BYTES + 1)
    response = await call(
        "POST", "/v1/parse/jobs", body=body, headers=_signed_headers("POST", "/v1/parse/jobs", body=body)
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == contract_errors.PAYLOAD_TOO_LARGE


async def test_fetch_file_stops_at_limit() -> None:
    """实际拉取超过上限立刻中断（不能等整个文件读完再判断，否则 10MB 限制形同虚设）。"""
    with FileServer() as server:
        with pytest.raises(contract_errors.ContractApiError) as exc:
            await fetch_file(server.url, max_bytes=64)
        assert exc.value.code == contract_errors.PAYLOAD_TOO_LARGE
