"""日志审计（系统管理 → 日志审计）：请求体 / 响应体关键字过滤。

``request_payload`` / ``response_json`` 是 JSONB，``LIKE`` 不能直接作用其上——service 先把
关键字取走、再把列 cast 成 text 做 ILIKE。这里锁住三件事：能按关键字命中、不存在的关键字
返回空、空串按「未筛选」处理（否则会退化成 ``ILIKE '%%'``：命中全表却看起来"没加条件"）。

库不可达时跳过，与同域台账用例同一判定。
"""

import uuid

from sqlalchemy import delete

from app.core.database import async_session_factory
from app.modules.system.api_log.model import SystemApiLogModel
from app.modules.system.api_log.schema import SystemApiLogQueryParam
from app.modules.system.api_log.service import SystemApiLogService
from app.modules.system.payload_filter import pop_like_keyword
from app.utils.common_util import search_to_dict
from tests.modules.system.test_access_trace import requires_db


def test_pop_like_keyword_treats_blank_as_unset() -> None:
    """空串 / 纯空格 / 未传都当作「没填」，并从条件字典里取走（模型上没有这一列）。"""
    data = search_to_dict(SystemApiLogQueryParam(request_body="  ", response_body="NO_EVIDENCE"))
    assert pop_like_keyword(data, "request_body") is None
    assert pop_like_keyword(data, "response_body") == "NO_EVIDENCE"
    assert pop_like_keyword({}, "request_body") is None
    assert "request_body" not in data
    assert "response_body" not in data


async def _seed_payload_row(rid: str) -> str:
    """造一条带报文的审计行，返回它的请求路径（列表 schema 不含 request_id）。"""
    path = f"/api/v1/test/payload/{rid}"
    async with async_session_factory() as session:
        session.add(
            SystemApiLogModel(
                username="tester",
                status=0,
                request_path=path,
                request_method="POST",
                response_code=200,
                request_id=rid,
                request_payload={"body": {"file_name": f"{rid}-payload.json"}},
                response_json={"code": 0, "data": {"entry_id": f"{rid}-answer"}},
            )
        )
        await session.commit()
    return path


async def _cleanup(rid: str) -> None:
    async with async_session_factory() as session:
        await session.execute(delete(SystemApiLogModel).where(SystemApiLogModel.request_id == rid))
        await session.commit()


@requires_db
async def test_api_log_page_filters_by_payload_keyword() -> None:
    """请求体 / 响应体关键字能命中 JSONB 报文；不存在的关键字返回空，空格不改变结果集。"""
    rid = f"apilog-{uuid.uuid4().hex[:12]}"
    path = await _seed_payload_row(rid)
    try:
        async with async_session_factory() as session:
            service = SystemApiLogService(session)
            by_request = await service.page(
                page_no=1, page_size=10, search=SystemApiLogQueryParam(request_path=path, request_body=f"{rid}-payload")
            )
            by_response = await service.page(
                page_no=1, page_size=10, search=SystemApiLogQueryParam(request_path=path, response_body=f"{rid}-answer")
            )
            absent = await service.page(
                page_no=1, page_size=10, search=SystemApiLogQueryParam(request_path=path, request_body=f"{rid}-absent")
            )
            blank = await service.page(
                page_no=1, page_size=10, search=SystemApiLogQueryParam(request_path=path, response_body="  ")
            )
        assert by_request.total == 1
        assert by_response.total == 1
        assert absent.total == 0
        assert blank.total == 1
    finally:
        await _cleanup(rid)
