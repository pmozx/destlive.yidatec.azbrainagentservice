"""管理端自助解析提交（``POST /api/v1/kb/parse-jobs/upload``）。

与契约 #01 的差别只在文件来源（multipart 上传 vs ``file_url``），所以这里验三件事：

1. 空文件 / 后缀白名单 / 大小上限都在**受理之前**归口成 400 / 413，不产生任务行；
2. 受理回执带 job_id，后台任务跑完后详情能看到解析产物；
3. ``file_meta`` 记录提交来源与解析选项（``submitted_by=admin``），便于与契约链路区分追溯。
"""

import uuid
from typing import Any

from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select

from app import create_app
from app.config.setting import settings
from app.core.database import async_session_factory
from app.core.table_bootstrap import ensure_pgvector_extension
from app.modules.kb.bootstrap import ensure_tables
from app.modules.kb.parse_job.model import KbParseJobModel
from app.modules.kb.parse_result.model import KbParseResultModel

from tests.support import call, requires_db

UPLOAD_PATH = "/api/v1/kb/parse-jobs/upload"


async def _prepare_tables() -> None:
    """补齐 kb 域表（与契约链路共用同一批表）。"""
    await ensure_pgvector_extension()
    await ensure_tables()


async def _upload(*, file_name: str, content: bytes, **form: str):
    """multipart 调用：ASGITransport 不走网络，后台任务在响应后由同一事件循环执行完。"""
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
        return await client.post(UPLOAD_PATH, files={"file": (file_name, content)}, data=form)


async def _cleanup(job_id: str) -> None:
    """清掉任务行与产物（管理端没有删除入口，只能按 job_id 直删）。"""
    async with async_session_factory() as session, session.begin():
        await session.execute(delete(KbParseResultModel).where(KbParseResultModel.job_id == job_id))
        await session.execute(delete(KbParseJobModel).where(KbParseJobModel.uuid == job_id))


async def _count_jobs() -> int:
    """当前任务行总数：用来断言被拒的提交没有留下半成品。"""
    async with async_session_factory() as session:
        return int((await session.execute(select(func.count()).select_from(KbParseJobModel))).scalar() or 0)


@requires_db
async def test_admin_upload_reaches_succeeded() -> None:
    """上传 md → 回执 → 后台解析完成：详情能读到解析产物与提交快照。"""
    await _prepare_tables()
    response = await _upload(
        file_name="vpn-guide.md",
        content="# VPN 申请\n\n在服务台提交申请，负责人审核后开通。\n\n## 排障\n\n先检查客户端版本。".encode(),
        resource_type="document",
        chunk="true",
        language="zh-CN",
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    created: dict[str, Any] = body["data"]
    job_id = created["uuid"]
    try:
        assert created["resource_type"] == "document"
        assert created["status"] in {"queued", "running", "succeeded"}

        detail = await call("GET", f"/api/v1/kb/parse-jobs/detail/{job_id}")
        assert detail.status_code == 200
        payload = detail.json()["data"]
        assert payload["status"] == "succeeded"
        assert payload["progress"] == 100
        # 管理端提交与契约 #01 用同一套 file_meta 口径，靠 submitted_by 区分来源
        assert payload["file_meta"]["submitted_by"] == "admin"
        assert payload["file_meta"]["file_name"] == "vpn-guide.md"
        assert payload["file_meta"]["file_size"] == len(
            "# VPN 申请\n\n在服务台提交申请，负责人审核后开通。\n\n## 排障\n\n先检查客户端版本。".encode()
        )
        assert payload["file_meta"]["options"] == {"chunk": True, "language": "zh-CN"}
        assert payload["result"]["chunks"]
    finally:
        await _cleanup(job_id)


@requires_db
async def test_admin_upload_rejects_before_creating_job() -> None:
    """空文件与不支持的后缀都在建任务之前拒绝，列表里不留半成品任务行。"""
    await _prepare_tables()
    before = await _count_jobs()

    empty = await _upload(file_name="empty.md", content=b"")
    assert empty.status_code == 400
    assert "为空" in empty.json()["msg"]

    suffix = await _upload(file_name="setup.exe", content=b"MZ")
    assert suffix.status_code == 400
    assert ".exe" in suffix.json()["msg"]

    assert await _count_jobs() == before


@requires_db
async def test_admin_upload_rejects_oversized_file(monkeypatch) -> None:
    """超过 ``PARSE_MAX_FILE_BYTES`` 立刻 413（读的时候就中断，不是读完再判）。"""
    await _prepare_tables()
    monkeypatch.setattr(settings, "PARSE_MAX_FILE_BYTES", 16)
    response = await _upload(file_name="big.md", content=b"# title\n\n" + b"x" * 64)
    assert response.status_code == 413
    assert "上限" in response.json()["msg"]


@requires_db
async def test_admin_upload_generates_unique_job_ids() -> None:
    """同文件重复上传是两个任务：管理端没有契约侧的 job_id 幂等键。"""
    await _prepare_tables()
    content = "# 同一份文件\n\n内容一致，但每次提交都应产生新任务。".encode()
    first = await _upload(file_name="same.md", content=content)
    second = await _upload(file_name="same.md", content=content)
    assert first.status_code == 200
    assert second.status_code == 200
    first_id = first.json()["data"]["uuid"]
    second_id = second.json()["data"]["uuid"]
    try:
        assert first_id != second_id
        assert uuid.UUID(first_id).version == 4
    finally:
        await _cleanup(first_id)
        await _cleanup(second_id)
