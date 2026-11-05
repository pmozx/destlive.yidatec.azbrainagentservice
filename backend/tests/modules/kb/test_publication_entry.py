"""发布单元正文写入 / 修订 / 停用：管理端对 03.1 / 03.3 / #20 的等价入口。

覆盖点与契约口径一致：
1. 首发写入：整篇正文切分后落库，指针切到新快照，03.2 基线能按同一 ``content_hash`` 回读；
2. 空壳补录：先「新增发布单元」再写正文，不能因为同 ID 已存在被 409 挡住；
3. 修订：新快照生效、旧快照与旧切片保留（可回读历史）；
4. 停用：切片与版本转 ``archived``、发布单元离 ``active``，正文保留供审计。

向量化在本模块内替换成确定性向量（不访问外部 embedding 服务），落库仍走真实
PostgreSQL + pgvector。
"""

import json
import uuid
from typing import Any

import pytest
from sqlalchemy import delete, select

from app.config.setting import settings
from app.core.clients.embedding import EmbeddingClient
from app.core.database import async_session_factory
from app.core.table_bootstrap import ensure_pgvector_extension
from app.modules.kb.bootstrap import ensure_tables
from app.modules.kb.chunk.model import KbChunkModel
from app.modules.kb.publication.model import KbPublicationModel
from app.modules.kb.revision.model import KbRevisionModel

from tests.support import call, requires_db

#: 四节正文合计约 1400 字符，按 PARSE_CHUNK_SIZE=800 必然切出多片，能验证「seq 连续」与「多片落库」
CONTENT = "# VPN 申请操作手册\n\n" + "\n\n".join(
    f"## 第{index}节\n\n"
    + "。".join(
        f"第{index}节第{item}条：在服务台提交申请后由负责人审核，并在服务台回复处理结果"
        for item in range(1, 11)
    )
    + "。"
    for index in range(1, 5)
)
REVISED_CONTENT = CONTENT + "\n\n## 附则\n\n申请被驳回时可在服务台查看驳回原因，修改后重新提交即可。"


@pytest.fixture(autouse=True)
def _stub_embedding(monkeypatch: pytest.MonkeyPatch):
    """确定性向量：第 i 条切片在第 i 维置 1，其余为 0（维度必须等于 VECTOR_DIM）。"""

    async def fake_embed_texts(self: EmbeddingClient, texts, *, batch_size: int | None = None):
        vectors: list[list[float]] = []
        for index, _ in enumerate(texts):
            vector = [0.0] * settings.VECTOR_DIM
            vector[index % settings.VECTOR_DIM] = 1.0
            vectors.append(vector)
        return vectors

    monkeypatch.setattr(EmbeddingClient, "embed_texts", fake_embed_texts)


async def _prepare_tables() -> None:
    """补齐 kb 域表。"""
    await ensure_pgvector_extension()
    await ensure_tables()


def _entry_body(**overrides) -> bytes:
    payload: dict[str, Any] = {
        "resource_type": "document",
        "tenant_id": "tenant-admin",
        "title": "VPN 申请操作手册",
        "category": "it",
        "summary": "员工 VPN 开通与排障",
        "tags": ["vpn", "手册"],
        "owner": "alice",
        "content": CONTENT,
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False).encode()


def _shell_body(publication_id: str) -> bytes:
    """「新增发布单元」建空壳：没有任何快照与切片。"""
    return json.dumps(
        {
            "publication_id": publication_id,
            "resource_type": "document",
            "title": "待补录的发布单元",
        },
        ensure_ascii=False,
    ).encode()


async def _cleanup(publication_id: str) -> None:
    async with async_session_factory() as session, session.begin():
        for model in (KbChunkModel, KbRevisionModel, KbPublicationModel):
            await session.execute(delete(model).where(model.publication_id == publication_id))


async def _chunk_rows(publication_id: str) -> list[KbChunkModel]:
    async with async_session_factory() as session:
        return list(
            (await session.execute(select(KbChunkModel).where(KbChunkModel.publication_id == publication_id)))
            .scalars()
            .all()
        )


async def _revision_rows(publication_id: str) -> list[KbRevisionModel]:
    async with async_session_factory() as session:
        return list(
            (await session.execute(select(KbRevisionModel).where(KbRevisionModel.publication_id == publication_id)))
            .scalars()
            .all()
        )


@requires_db
async def test_write_entry_creates_publication_with_slices() -> None:
    """首发写入：切片与向量落库、指针生效、03.2 基线按同一摘要回读。"""
    await _prepare_tables()
    publication_id = f"kb-entry-{uuid.uuid4().hex[:12]}"
    try:
        response = await call(
            "POST", "/api/v1/kb/publication/entry", body=_entry_body(publication_id=publication_id)
        )
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["publication_id"] == publication_id
        assert data["revision"] == 1
        assert data["chunk_count"] >= 2
        assert data["searchable"] is True
        assert data["status"] == "active"
        assert data["content_hash"].startswith("sha256:")

        detail = (await call("GET", f"/api/v1/kb/publication/detail/{publication_id}")).json()["data"]
        assert detail["current_record_id"] == data["record_id"]
        assert detail["current_revision"] == 1
        assert detail["version_count"] == 1
        assert detail["chunk_count"] == data["chunk_count"]
        assert detail["title"] == "VPN 申请操作手册"

        baseline = (await call("GET", f"/api/v1/kb/publication/baseline/{publication_id}")).json()["data"]
        assert baseline["content_hash"] == data["content_hash"]
        assert [chunk["seq"] for chunk in baseline["chunks"]] == list(range(1, data["chunk_count"] + 1))
        # 切片标题路径取自 Markdown 小标题：与解析链路共用 split_text 的直接证据
        assert any(chunk["heading"] for chunk in baseline["chunks"])

        rows = await _chunk_rows(publication_id)
        assert [row.seq for row in rows] == list(range(1, data["chunk_count"] + 1))
        assert all(row.chunk_id and row.status == "online" for row in rows)
        assert all(row.embedding is not None and len(row.embedding) == settings.VECTOR_DIM for row in rows)
    finally:
        await _cleanup(publication_id)


@requires_db
async def test_write_entry_generates_publication_id() -> None:
    """不传 publication_id 时由服务端生成，返回的回执与详情对得上。"""
    await _prepare_tables()
    publication_id = ""
    try:
        response = await call("POST", "/api/v1/kb/publication/entry", body=_entry_body())
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        publication_id = data["publication_id"]
        assert publication_id.startswith("kb-")
        detail = (await call("GET", f"/api/v1/kb/publication/detail/{publication_id}")).json()["data"]
        assert detail["current_record_id"] == data["record_id"]
    finally:
        if publication_id:
            await _cleanup(publication_id)


@requires_db
async def test_write_entry_fills_shell_publication() -> None:
    """先建空壳再写正文：按修订语义补录首版，租户与展示字段一并补齐。"""
    await _prepare_tables()
    publication_id = f"kb-entry-{uuid.uuid4().hex[:12]}"
    try:
        shell = await call("POST", "/api/v1/kb/publication/create", body=_shell_body(publication_id))
        assert shell.status_code == 200, shell.text
        assert shell.json()["data"]["current_revision"] == 0

        response = await call(
            "POST",
            "/api/v1/kb/publication/entry",
            body=_entry_body(publication_id=publication_id, tenant_id="tenant-filled"),
        )
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["revision"] == 1
        assert data["chunk_count"] >= 2

        detail = (await call("GET", f"/api/v1/kb/publication/detail/{publication_id}")).json()["data"]
        assert detail["current_record_id"] == data["record_id"]
        assert detail["current_revision"] == 1
        assert detail["version_count"] == 1
        assert detail["title"] == "VPN 申请操作手册"
        assert detail["tenant_id"] == "tenant-filled"
    finally:
        await _cleanup(publication_id)


@requires_db
async def test_write_entry_rejects_when_snapshot_exists() -> None:
    """已有生效快照再走写入属误用：409，且不产生第二个快照。"""
    await _prepare_tables()
    publication_id = f"kb-entry-{uuid.uuid4().hex[:12]}"
    try:
        first = await call(
            "POST", "/api/v1/kb/publication/entry", body=_entry_body(publication_id=publication_id)
        )
        assert first.status_code == 200, first.text

        second = await call(
            "POST", "/api/v1/kb/publication/entry", body=_entry_body(publication_id=publication_id)
        )
        assert second.status_code == 409
        assert "修订" in second.json()["msg"]
        assert len(await _revision_rows(publication_id)) == 1
    finally:
        await _cleanup(publication_id)


@requires_db
async def test_revise_entry_keeps_old_snapshot() -> None:
    """修订：新快照生效、旧快照与旧切片保留，展示字段同步更新。"""
    await _prepare_tables()
    publication_id = f"kb-entry-{uuid.uuid4().hex[:12]}"
    try:
        first = await call(
            "POST", "/api/v1/kb/publication/entry", body=_entry_body(publication_id=publication_id)
        )
        assert first.status_code == 200, first.text
        first_data = first.json()["data"]

        revised = await call(
            "PUT",
            f"/api/v1/kb/publication/entry/{publication_id}",
            body=json.dumps(
                {
                    "content": REVISED_CONTENT,
                    "base_record_id": first_data["record_id"],
                    "title": "VPN 申请操作手册（v2）",
                },
                ensure_ascii=False,
            ).encode(),
        )
        assert revised.status_code == 200, revised.text
        revised_data = revised.json()["data"]
        assert revised_data["revision"] == 2
        assert revised_data["record_id"] != first_data["record_id"]

        detail = (await call("GET", f"/api/v1/kb/publication/detail/{publication_id}")).json()["data"]
        assert detail["current_record_id"] == revised_data["record_id"]
        assert detail["current_revision"] == 2
        assert detail["version_count"] == 2
        assert detail["title"] == "VPN 申请操作手册（v2）"

        rows = await _chunk_rows(publication_id)
        old_rows = [row for row in rows if row.record_id == first_data["record_id"]]
        new_rows = [row for row in rows if row.record_id == revised_data["record_id"]]
        assert len(old_rows) == first_data["chunk_count"]
        assert len(new_rows) == revised_data["chunk_count"]
        # 旧快照不被归档：仍可被 03.2 按 record_id 历史回读
        assert all(row.status == "online" for row in old_rows)

        historical = (
            await call(
                "GET",
                f"/api/v1/kb/publication/baseline/{publication_id}?record_id={first_data['record_id']}",
            )
        ).json()["data"]
        assert historical["content_hash"] == first_data["content_hash"]
    finally:
        await _cleanup(publication_id)


@requires_db
async def test_revise_entry_rejects_stale_base() -> None:
    """base_record_id 与当前基线不一致 → 409（并发下不覆盖别人已发布的版本）。"""
    await _prepare_tables()
    publication_id = f"kb-entry-{uuid.uuid4().hex[:12]}"
    try:
        first = await call(
            "POST", "/api/v1/kb/publication/entry", body=_entry_body(publication_id=publication_id)
        )
        assert first.status_code == 200, first.text

        stale = await call(
            "PUT",
            f"/api/v1/kb/publication/entry/{publication_id}",
            body=json.dumps(
                {"content": REVISED_CONTENT, "base_record_id": "admin-stale-snapshot"}, ensure_ascii=False
            ).encode(),
        )
        assert stale.status_code == 409
        assert "基线已变化" in stale.json()["msg"]
        assert len(await _revision_rows(publication_id)) == 1
    finally:
        await _cleanup(publication_id)


@requires_db
async def test_revise_entry_requires_snapshot() -> None:
    """空壳发布单元直接走修订 → 409，提示先写入正文。"""
    await _prepare_tables()
    publication_id = f"kb-entry-{uuid.uuid4().hex[:12]}"
    try:
        created = await call("POST", "/api/v1/kb/publication/create", body=_shell_body(publication_id))
        assert created.status_code == 200

        response = await call(
            "PUT",
            f"/api/v1/kb/publication/entry/{publication_id}",
            body=json.dumps({"content": CONTENT}, ensure_ascii=False).encode(),
        )
        assert response.status_code == 409
        assert "先写入正文" in response.json()["msg"]
    finally:
        await _cleanup(publication_id)


@requires_db
async def test_deactivate_archives_chunks_and_revision() -> None:
    """停用（#20）：切片与版本归档、发布单元离 active，三处开关同时失效。"""
    await _prepare_tables()
    publication_id = f"kb-entry-{uuid.uuid4().hex[:12]}"
    try:
        written = await call(
            "POST", "/api/v1/kb/publication/entry", body=_entry_body(publication_id=publication_id)
        )
        assert written.status_code == 200, written.text
        record_id = written.json()["data"]["record_id"]

        response = await call(
            "POST",
            f"/api/v1/kb/publication/deactivate/{publication_id}",
            body=json.dumps({"reason": "内容已并入新版手册"}, ensure_ascii=False).encode(),
        )
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["record_id"] == record_id
        assert data["revision"] == 2
        assert data["deactivated"] is True
        assert data["searchable"] is False
        assert data["status"] == "inactive"

        detail = (await call("GET", f"/api/v1/kb/publication/detail/{publication_id}")).json()["data"]
        assert detail["status"] == "inactive"
        assert detail["current_record_id"] == record_id

        assert all(row.status == "archived" for row in await _chunk_rows(publication_id))
        revisions = await _revision_rows(publication_id)
        assert len(revisions) == 1
        assert (revisions[0].status, revisions[0].searchable) == ("archived", False)

        # 重复停用：发布单元已 inactive，按 409 拒绝而不是「静默成功」
        again = await call(
            "POST",
            f"/api/v1/kb/publication/deactivate/{publication_id}",
            body=json.dumps({"reason": "再次停用"}, ensure_ascii=False).encode(),
        )
        assert again.status_code == 409
    finally:
        await _cleanup(publication_id)


@requires_db
async def test_deactivate_requires_snapshot() -> None:
    """空壳发布单元没有可停用的快照 → 400。"""
    await _prepare_tables()
    publication_id = f"kb-entry-{uuid.uuid4().hex[:12]}"
    try:
        created = await call("POST", "/api/v1/kb/publication/create", body=_shell_body(publication_id))
        assert created.status_code == 200

        response = await call(
            "POST",
            f"/api/v1/kb/publication/deactivate/{publication_id}",
            body=json.dumps({"reason": "误建空壳"}, ensure_ascii=False).encode(),
        )
        assert response.status_code == 400
        assert "不能停用" in response.json()["msg"]
    finally:
        await _cleanup(publication_id)


@requires_db
async def test_write_entry_rejects_blank_content() -> None:
    """正文为空白 → 在参数校验阶段就被拒，不产生任何业务行。"""
    await _prepare_tables()
    publication_id = f"kb-entry-{uuid.uuid4().hex[:12]}"
    try:
        response = await call(
            "POST",
            "/api/v1/kb/publication/entry",
            body=_entry_body(publication_id=publication_id, content="   \n  "),
        )
        assert response.status_code in (400, 422)
        async with async_session_factory() as session:
            exists = (
                await session.execute(
                    select(KbPublicationModel).where(KbPublicationModel.publication_id == publication_id)
                )
            ).scalar_one_or_none()
        assert exists is None
    finally:
        await _cleanup(publication_id)
