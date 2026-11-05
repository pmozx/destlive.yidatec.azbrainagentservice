"""发布单元来源与关联（只读溯源）：写入渠道判定 + 按 ``file_meta.doc_id`` 反查解析任务。

发布单元与解析任务之间**没有库级关联**（``02-数据模型与表关系.md`` §1.2「内容与作业分离」），
所以本接口只回「能核对得上的线索」：

1. 写入渠道靠 ``current_record_id`` 的 ``admin-`` 前缀判定（契约侧 record_id 由 GSI 生成）；
2. 解析任务靠 ``parse_job.file_meta->>'doc_id'`` 等于 ``publication.resource_id`` 匹配；
3. 匹配不到时返回空列表 + 口径说明，而不是报错或伪造关联。
"""

import json
import uuid
from typing import Any

import pytest
from sqlalchemy import delete

from app.config.setting import settings
from app.core.clients.embedding import EmbeddingClient
from app.core.database import async_session_factory
from app.core.table_bootstrap import ensure_pgvector_extension
from app.modules.kb.bootstrap import ensure_tables
from app.modules.kb.chunk.model import KbChunkModel
from app.modules.kb.parse_job.model import KbParseJobModel
from app.modules.kb.publication.model import KbPublicationModel
from app.modules.kb.revision.model import KbRevisionModel

from tests.support import call, requires_db

CONTENT = "# 报销流程\n\n" + "\n\n".join(
    f"## 第{index}节\n\n" + "。".join(f"第{index}节第{item}条：提交后在系统内审批" for item in range(1, 6)) + "。"
    for index in range(1, 3)
)


@pytest.fixture(autouse=True)
def _stub_embedding(monkeypatch: pytest.MonkeyPatch):
    """确定性向量：不访问外部 embedding 服务，落库仍走真实 pgvector。"""

    async def fake_embed_texts(self: EmbeddingClient, texts, *, batch_size: int | None = None):
        vectors: list[list[float]] = []
        for index, _ in enumerate(texts):
            vector = [0.0] * settings.VECTOR_DIM
            vector[index % settings.VECTOR_DIM] = 1.0
            vectors.append(vector)
        return vectors

    monkeypatch.setattr(EmbeddingClient, "embed_texts", fake_embed_texts)


async def _prepare_tables() -> None:
    await ensure_pgvector_extension()
    await ensure_tables()


def _entry_body(**overrides) -> bytes:
    payload: dict[str, Any] = {
        "resource_type": "document",
        "tenant_id": "tenant-source",
        "title": "报销流程",
        "content": CONTENT,
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False).encode()


async def _cleanup(publication_id: str, job_ids: list[str]) -> None:
    async with async_session_factory() as session, session.begin():
        for model in (KbChunkModel, KbRevisionModel, KbPublicationModel):
            await session.execute(delete(model).where(model.publication_id == publication_id))
        if job_ids:
            await session.execute(delete(KbParseJobModel).where(KbParseJobModel.uuid.in_(job_ids)))


async def _insert_parse_job(*, job_id: str, doc_id: str | None) -> None:
    """直接落一条解析任务行：契约 #01 把 ``doc_id`` 放在 ``file_meta`` 里，没有独立列。"""
    file_meta: dict[str, Any] = {"file_name": "fapiao.pdf", "file_url": "https://example.test/fapiao.pdf"}
    if doc_id:
        file_meta["doc_id"] = doc_id
    async with async_session_factory() as session, session.begin():
        session.add(
            KbParseJobModel(
                uuid=job_id,
                resource_type="document",
                file_meta=file_meta,
                progress=100,
                status="succeeded",
            )
        )


@requires_db
async def test_source_matches_parse_job_by_doc_id() -> None:
    """按 ``file_meta.doc_id`` 命中解析任务，并把写入渠道判为 admin。"""
    await _prepare_tables()
    publication_id = f"kb-source-{uuid.uuid4().hex[:12]}"
    resource_id = f"DOC-{uuid.uuid4().hex[:10]}"
    job_id = f"job-{uuid.uuid4().hex[:12]}"
    try:
        await _insert_parse_job(job_id=job_id, doc_id=resource_id)

        written = await call(
            "POST",
            "/api/v1/kb/publication/entry",
            body=_entry_body(publication_id=publication_id, resource_id=resource_id),
        )
        assert written.status_code == 200, written.text

        data = (await call("GET", f"/api/v1/kb/publication/source/{publication_id}")).json()["data"]
        assert data["publication_id"] == publication_id
        assert data["resource_id"] == resource_id
        assert data["write_channel"] == "admin"
        assert [job["job_id"] for job in data["parse_jobs"]] == [job_id]
        assert data["parse_jobs"][0]["status"] == "succeeded"
        assert data["parse_jobs"][0]["matched_by"] == "file_meta.doc_id"
        assert "不构成强关联" in data["hint"]
    finally:
        await _cleanup(publication_id, [job_id])


@requires_db
async def test_source_returns_empty_jobs_when_no_match() -> None:
    """匹配不到解析任务时返回空列表，并把口径写进 hint（不伪造关联）。"""
    await _prepare_tables()
    publication_id = f"kb-source-{uuid.uuid4().hex[:12]}"
    try:
        written = await call(
            "POST",
            "/api/v1/kb/publication/entry",
            body=_entry_body(publication_id=publication_id, resource_id=f"DOC-{uuid.uuid4().hex[:10]}"),
        )
        assert written.status_code == 200, written.text

        data = (await call("GET", f"/api/v1/kb/publication/source/{publication_id}")).json()["data"]
        assert data["parse_jobs"] == []
        assert data["write_channel"] == "admin"
        assert "管理台上传的解析任务没有 doc_id" in data["hint"]
    finally:
        await _cleanup(publication_id, [])


@requires_db
async def test_source_reports_pending_channel_for_shell() -> None:
    """空壳发布单元没有快照，渠道只能是 pending。"""
    await _prepare_tables()
    publication_id = f"kb-source-{uuid.uuid4().hex[:12]}"
    try:
        shell = await call(
            "POST",
            "/api/v1/kb/publication/create",
            body=json.dumps(
                {"publication_id": publication_id, "resource_type": "document", "title": "空壳"},
                ensure_ascii=False,
            ).encode(),
        )
        assert shell.status_code == 200, shell.text

        data = (await call("GET", f"/api/v1/kb/publication/source/{publication_id}")).json()["data"]
        assert data["write_channel"] == "pending"
        assert data["current_record_id"] is None
    finally:
        await _cleanup(publication_id, [])


@requires_db
async def test_source_unknown_publication_returns_404() -> None:
    """发布单元不存在时按 404 返回，与详情接口口径一致。"""
    await _prepare_tables()
    response = await call("GET", f"/api/v1/kb/publication/source/kb-missing-{uuid.uuid4().hex[:8]}")
    assert response.status_code == 404, response.text


@requires_db
async def test_list_filters_by_resource_id() -> None:
    """列表支持按 ``resource_id`` 精确过滤：解析任务详情反向查发布单元的入口。"""
    await _prepare_tables()
    publication_id = f"kb-source-{uuid.uuid4().hex[:12]}"
    resource_id = f"DOC-{uuid.uuid4().hex[:10]}"
    try:
        written = await call(
            "POST",
            "/api/v1/kb/publication/entry",
            body=_entry_body(publication_id=publication_id, resource_id=resource_id),
        )
        assert written.status_code == 200, written.text

        listed = (
            await call("GET", f"/api/v1/kb/publication/list?resource_id={resource_id}&page_size=20")
        ).json()["data"]
        assert [row["publication_id"] for row in listed["items"]] == [publication_id]
    finally:
        await _cleanup(publication_id, [])
