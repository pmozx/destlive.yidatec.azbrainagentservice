"""接入调用追踪（系统管理 → 接入调用追踪）：路由登记、JSONB 过滤、四表串联与概览口径。

页面本身没有业务写入，因此这里验证三件事：
1. 七个只读接口都挂在 /api/v1/system/access-trace 下，且 HTTP 层能打通；
2. `security_check` 里的签名结果 / nonce 状态能作为过滤条件（JSONB 子字段，不是普通列）；
3. `timeline` 能按 request_id 把调用、防重放、幂等（含 `request_id-*` 业务键）、结果台账串起来。

DB 类用例在数据库不可达时自动跳过。
"""

import socket
import uuid
from datetime import UTC, datetime, timedelta
from functools import lru_cache

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from app import create_app
from app.config.setting import settings
from app.core.database import async_session_factory
from app.core.exceptions import CustomException
from app.modules.system.access_trace.schema import AccessTraceQueryParam, TraceSummarySchema, TraceTimelineSchema
from app.modules.system.access_trace.service import AccessTraceService
from app.modules.system.api_log.model import SystemApiLogModel
from app.modules.system.models import SystemIdempotencyModel, SystemNonceModel, SystemRequestLogModel
from app.utils.common_util import search_to_dict

TRACE_PATHS: set[str] = {
    "/api/v1/system/access-trace/summary",
    "/api/v1/system/access-trace/calls",
    "/api/v1/system/access-trace/calls/detail/{id}",
    "/api/v1/system/access-trace/nonces",
    "/api/v1/system/access-trace/idempotency",
    "/api/v1/system/access-trace/request-logs",
    "/api/v1/system/access-trace/timeline",
}


@lru_cache(maxsize=1)
def _db_reachable() -> bool:
    """四表查询依赖真实 PostgreSQL（JSONB 子字段过滤）：不可达就跳过，本地无库时不红一片。"""
    try:
        with socket.create_connection((settings.DATABASE_HOST, settings.DATABASE_PORT), timeout=2):
            return True
    except OSError:
        return False


requires_db = pytest.mark.skipif(not _db_reachable(), reason="数据库不可达，跳过台账用例")


def _rid() -> str:
    """每次用例独立的追踪 ID，避免与历史台账撞唯一约束。"""
    return f"trace-{uuid.uuid4().hex[:12]}"


async def _seed(rid: str) -> None:
    """造一条完整的调用链：被拒的调用 + nonce 首用 + 幂等台账（业务键带前缀）+ 结果台账。"""
    now = datetime.now(UTC)
    async with async_session_factory() as session:
        session.add(
            SystemApiLogModel(
                username="gsi-service",
                status=1,
                request_path="/v1/knowledge/search",
                request_method="POST",
                response_code=401,
                auth_mode="hmac",
                client_id="gsi-service",
                request_id=rid,
                security_check={"signature_result": "failed", "nonce_state": "reused", "failure_code": "UNAUTHORIZED"},
            )
        )
        session.add(
            SystemNonceModel(
                client_id="gsi-service", nonce=f"nonce-{uuid.uuid4().hex}", request_id=rid, expires_at=now + timedelta(minutes=10)
            )
        )
        session.add(
            SystemIdempotencyModel(
                scope="request", biz_id=f"{rid}-request_id", body_hash="a" * 64, status="processing", expires_at=now + timedelta(days=1)
            )
        )
        session.add(
            SystemRequestLogModel(
                request_id=rid, operation="completion", status="running", result_visible=True, expires_at=now + timedelta(hours=24)
            )
        )
        await session.commit()


async def _cleanup(rid: str) -> None:
    async with async_session_factory() as session:
        await session.execute(delete(SystemApiLogModel).where(SystemApiLogModel.request_id == rid))
        await session.execute(delete(SystemNonceModel).where(SystemNonceModel.request_id == rid))
        await session.execute(delete(SystemIdempotencyModel).where(SystemIdempotencyModel.biz_id.like(f"{rid}%")))
        await session.execute(delete(SystemRequestLogModel).where(SystemRequestLogModel.request_id == rid))
        await session.commit()


def test_seven_readonly_routes_are_registered() -> None:
    """七个接口路径在 OpenAPI 中存在（页面四个标签页 + 详情 + 追踪 + 概览）。"""
    paths = set(create_app().openapi()["paths"])
    assert TRACE_PATHS <= paths


def test_query_param_jsonb_fields_stay_raw() -> None:
    """普通列走 (操作符, 值)，JSONB 内部字段不带 q 需由 service 单独拼条件。"""
    data = search_to_dict(AccessTraceQueryParam(request_id="abc", status=1, signature_result="failed"))
    assert data["request_id"] == ("like", "abc")
    assert data["status"] == ("eq", 1)
    assert data["signature_result"] == "failed"


@requires_db
async def test_calls_page_filters_by_signature_result() -> None:
    """`security_check->>'signature_result'` 能作为过滤条件（不是列，走不了 CRUDBase 的 eq）。"""
    rid = _rid()
    await _seed(rid)
    try:
        async with async_session_factory() as session:
            page = await AccessTraceService(session).page_calls(
                page_no=1,
                page_size=10,
                search=AccessTraceQueryParam(request_id=rid, signature_result="failed"),
            )
        assert page.total == 1
        assert page.items[0].request_id == rid
        assert page.items[0].security_check["failure_code"] == "UNAUTHORIZED"

        async with async_session_factory() as session:
            passed = await AccessTraceService(session).page_calls(
                page_no=1,
                page_size=10,
                search=AccessTraceQueryParam(request_id=rid, signature_result="passed"),
            )
        assert passed.total == 0
    finally:
        await _cleanup(rid)


@requires_db
async def test_calls_defaults_to_contract_paths_only() -> None:
    """页面口径是 GSI 契约调用：不传 request_path 时只看 /v1，显式传路径才回溯管理台记录。"""
    rid = _rid()
    admin_rid = f"{rid}-admin"
    await _seed(rid)
    try:
        async with async_session_factory() as session:
            session.add(
                SystemApiLogModel(
                    username="anonymous",
                    status=0,
                    request_path="/api/v1/system/api-log/list",
                    request_method="GET",
                    response_code=200,
                    request_id=admin_rid,
                )
            )
            await session.commit()

        async with async_session_factory() as session:
            default_scope = await AccessTraceService(session).page_calls(
                page_no=1, page_size=10, search=AccessTraceQueryParam(request_id=rid)
            )
        assert [row.request_id for row in default_scope.items] == [rid]

        async with async_session_factory() as session:
            explicit = await AccessTraceService(session).page_calls(
                page_no=1, page_size=10, search=AccessTraceQueryParam(request_path="/api/v1/system/api-log/list")
            )
        assert admin_rid in [row.request_id for row in explicit.items]
    finally:
        await _cleanup(rid)
        await _cleanup(admin_rid)


@requires_db
async def test_timeline_links_four_tables() -> None:
    """按 request_id 一次串起四张表：幂等台账的业务键 `rid-request_id` 也要被前缀匹配到。"""
    rid = _rid()
    await _seed(rid)
    try:
        async with async_session_factory() as session:
            timeline = await AccessTraceService(session).timeline(rid)
        assert isinstance(timeline, TraceTimelineSchema)
        assert timeline.found is True
        assert len(timeline.calls) == 1
        assert len(timeline.nonces) == 1
        assert [row.biz_id for row in timeline.idempotency] == [f"{rid}-request_id"]
        assert len(timeline.request_logs) == 1
    finally:
        await _cleanup(rid)


@requires_db
async def test_timeline_with_unknown_id_reports_not_found() -> None:
    """陌生 ID 不报错、返回空时间线（页面据此提示"未找到"）。"""
    async with async_session_factory() as session:
        timeline = await AccessTraceService(session).timeline(f"nope-{uuid.uuid4().hex[:8]}")
    assert timeline.found is False
    assert not (timeline.calls or timeline.nonces or timeline.idempotency or timeline.request_logs)


@requires_db
async def test_summary_counts_seeded_call() -> None:
    """近 24 小时概览能统计到刚造的契约调用（签名失败 + 重放 + 409 口径）。"""
    rid = _rid()
    await _seed(rid)
    try:
        async with async_session_factory() as session:
            summary = await AccessTraceService(session).summary(hours=24)
        assert isinstance(summary, TraceSummarySchema)
        assert summary.window_hours == 24
        assert summary.call_total >= 1
        assert summary.signature_failed >= 1
        assert summary.nonce_reused >= 1
    finally:
        await _cleanup(rid)


@requires_db
async def test_call_detail_raises_for_missing_row() -> None:
    """详情查不到时按业务异常返回（页面提示，不是 500）。"""
    async with async_session_factory() as session:
        with pytest.raises(CustomException):
            await AccessTraceService(session).call_detail(id=2**31 - 1)


@requires_db
async def test_trace_endpoints_answer_over_http() -> None:
    """HTTP 冒烟：概览与调用明细分页均返回统一响应结构。"""
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
        summary = await client.get("/api/v1/system/access-trace/summary", params={"hours": 24})
        calls = await client.get("/api/v1/system/access-trace/calls", params={"page_no": 1, "page_size": 5})
        timeline = await client.get("/api/v1/system/access-trace/timeline", params={"request_id": "trace-http-smoke"})

    assert summary.status_code == 200
    assert summary.json()["code"] == 0
    assert summary.json()["data"]["window_hours"] == 24

    assert calls.status_code == 200
    assert "items" in calls.json()["data"]

    assert timeline.status_code == 200
    assert timeline.json()["data"]["request_id"] == "trace-http-smoke"
