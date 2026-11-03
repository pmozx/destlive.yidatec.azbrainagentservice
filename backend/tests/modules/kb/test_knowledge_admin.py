"""知识维护管理端：CRUD 全流程 + 级联删除 + 唯一冲突。

合并 ``sbp_ai_kb_entry_meta`` 后，知识条目统一使用 ``sbp_ai_kb_publication`` 一张表。
测试仍打 ``@requires_db``：先幂等补齐 kb 表，再走进程内 ASGI 调真实接口，最后按 publication_id 清理。
"""

import json
import uuid

from sqlalchemy import delete, func, select

from app.core.database import async_session_factory
from app.core.table_bootstrap import ensure_pgvector_extension
from app.modules.kb.bootstrap import ensure_tables
from app.modules.kb.chunk.model import KbChunkModel
from app.modules.kb.publication.model import KbPublicationModel
from app.modules.kb.revision.model import KbRevisionModel

from tests.support import call, requires_db


def _body(**overrides) -> bytes:
    payload = {
        "publication_id": f"kb-admin-{uuid.uuid4().hex[:12]}",
        "resource_type": "document",
        "tenant_id": "tenant-a",
        "resource_id": "res-1",
        "title": "VPN 申请操作手册",
        "category": "it",
        "summary": "员工 VPN 开通与排障",
        "tags": ["vpn", "手册"],
        "owner": "alice",
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False).encode()


async def _prepare_tables() -> None:
    """补齐 kb 域表。"""
    await ensure_pgvector_extension()
    await ensure_tables()


async def _cleanup(publication_id: str) -> None:
    async with async_session_factory() as session, session.begin():
        for model in (KbChunkModel, KbRevisionModel, KbPublicationModel):
            await session.execute(delete(model).where(model.publication_id == publication_id))


async def _count(publication_id: str) -> dict[str, int]:
    async with async_session_factory() as session:
        counts: dict[str, int] = {}
        for key, model in (
            ("chunks", KbChunkModel),
            ("revisions", KbRevisionModel),
            ("publications", KbPublicationModel),
        ):
            counts[key] = int(
                (
                    await session.execute(
                        select(func.count()).select_from(model).where(model.publication_id == publication_id)
                    )
                ).scalar()
                or 0
            )
        return counts


@requires_db
async def test_knowledge_admin_crud_flow() -> None:
    """新增 → 查询 → 编辑 → 上下线 → 删除：标准 CRUD 全流程。"""
    await _prepare_tables()
    body = _body()
    publication_id = json.loads(body)["publication_id"]
    try:
        created = await call("POST", "/api/v1/kb/publication/create", body=body)
        assert created.status_code == 200
        created_data = created.json()["data"]
        assert created_data["publication_id"] == publication_id
        assert created_data["status"] == "active"
        assert created_data["current_revision"] == 0
        assert created_data["title"] == "VPN 申请操作手册"

        listed = await call("GET", "/api/v1/kb/publication/list?title=VPN&status=active")
        assert listed.status_code == 200
        page = listed.json()["data"]
        assert page["total"] == 1
        assert page["items"][0]["title"] == "VPN 申请操作手册"

        detail = await call("GET", f"/api/v1/kb/publication/detail/{publication_id}")
        assert detail.status_code == 200
        assert detail.json()["data"]["category"] == "it"

        updated = await call(
            "PUT",
            f"/api/v1/kb/publication/update/{publication_id}",
            body=json.dumps({"title": "VPN 手册（新版）", "summary": None}).encode(),
        )
        assert updated.status_code == 200
        assert updated.json()["data"]["title"] == "VPN 手册（新版）"
        assert updated.json()["data"]["summary"] is None

        status_changed = await call(
            "PATCH",
            f"/api/v1/kb/publication/status/{publication_id}",
            body=json.dumps({"status": "inactive"}).encode(),
        )
        assert status_changed.status_code == 200
        assert status_changed.json()["data"]["status"] == "inactive"

        versions = await call("GET", f"/api/v1/kb/publication/versions/{publication_id}")
        assert versions.status_code == 200
        assert versions.json()["data"]["total"] == 0

        deleted = await call("DELETE", f"/api/v1/kb/publication/delete/{publication_id}")
        assert deleted.status_code == 200

        missing = await call("GET", f"/api/v1/kb/publication/detail/{publication_id}")
        assert missing.status_code == 404
        assert await _count(publication_id) == {"chunks": 0, "revisions": 0, "publications": 0}
    finally:
        await _cleanup(publication_id)


@requires_db
async def test_knowledge_create_rejects_duplicate_publication_id() -> None:
    """同一个 publication_id 重复创建必须 409，不能隐式覆盖契约链路已写入的发布单元。"""
    await _prepare_tables()
    body = _body()
    publication_id = json.loads(body)["publication_id"]
    try:
        first = await call("POST", "/api/v1/kb/publication/create", body=body)
        second = await call("POST", "/api/v1/kb/publication/create", body=body)
        assert first.status_code == 200
        assert second.status_code == 409
        assert "已存在" in second.json()["msg"]
    finally:
        await _cleanup(publication_id)


@requires_db
async def test_knowledge_delete_cascades_versions_and_chunks() -> None:
    """删除条目要物理清掉版本与切片，不能留下无法召回的悬挂数据。"""
    await _prepare_tables()
    publication_id = f"kb-admin-{uuid.uuid4().hex[:12]}"
    try:
        created = await call("POST", "/api/v1/kb/publication/create", body=_body(publication_id=publication_id))
        assert created.status_code == 200

        async with async_session_factory() as session, session.begin():
            session.add(
                KbRevisionModel(
                    publication_id=publication_id,
                    record_id="record-1",
                    revision=1,
                    status="online",
                    chunk_count=1,
                )
            )
            session.add(
                KbChunkModel(
                    publication_id=publication_id,
                    record_id="record-1",
                    revision=1,
                    seq=1,
                    content="切片正文",
                    content_hash="a" * 64,
                    status="online",
                )
            )
        assert (await _count(publication_id))["chunks"] == 1

        deleted = await call("DELETE", f"/api/v1/kb/publication/delete/{publication_id}")
        assert deleted.status_code == 200
        assert await _count(publication_id) == {"chunks": 0, "revisions": 0, "publications": 0}
    finally:
        await _cleanup(publication_id)
