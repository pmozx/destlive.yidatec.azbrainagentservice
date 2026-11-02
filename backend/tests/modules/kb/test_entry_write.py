"""知识写入链路回归（T3.1–T3.3）：#03 首发、#05 修订、#06 整单删除。

验收断言（`03-POC下线与契约迁移方案.md` §3.3）：
1. 首发 201；2. 同 request_id 同正文回放 201；3. 同 publication_id 不同 request_id → 409；
4. content_hash 与切片集合不符 → 409；5. 向量条数 = 切片条数；6. 删除后不可召回。

向量化依赖在本模块内替换成确定性向量（不访问外部 embedding 服务），
落库仍走真实 PostgreSQL + pgvector，保证「写入行数 / 向量维度 / 状态机」被真实校验。
"""

import json
import socket
import uuid
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app import create_app
from app.config.setting import settings
from app.core.clients.embedding import EmbeddingClient
from app.core.database import async_session_factory
from app.core.security_signature import build_signed_headers
from app.modules_api.contract import errors as contract_errors
from app.modules_api.contract.knowledge.schema import canonical_content_hash
from app.modules.kb.chunk.model import KbChunkModel
from app.modules.kb.publication.model import KbPublicationModel
from app.modules.kb.revision.model import KbRevisionModel
from app.modules.system.idempotency.model import SystemIdempotencyModel

def _db_reachable() -> bool:
    """知识写入用例依赖真实 PostgreSQL + pgvector：不可达就跳过，不让本地无库时红一片。"""
    try:
        with socket.create_connection((settings.DATABASE_HOST, settings.DATABASE_PORT), timeout=2):
            return True
    except OSError:
        return False


requires_db = pytest.mark.skipif(not _db_reachable(), reason="数据库不可达，跳过知识写入用例")

CHUNKS: list[dict[str, Any]] = [
    {
        "chunk_id": "record:1",
        "seq": 1,
        "heading": "申请流程",
        "content": "在服务台提交 vpn 申请，由负责人审核后开通账号。",
        "page_from": 1,
        "page_to": 1,
    },
    {
        "chunk_id": "record:2",
        "seq": 2,
        "heading": "审批时限",
        "content": "普通申请 1 个工作日内完成审核，涉及生产网段的申请需额外由安全组复核。",
        "page_from": 1,
        "page_to": 2,
    },
    {
        "chunk_id": "record:3",
        "seq": 3,
        "heading": "常见问题",
        "content": "申请被驳回时可在服务台查看驳回原因，修改后重新提交即可。",
        "page_from": 2,
        "page_to": 2,
    },
]


def _chunks(record_id: str) -> list[dict[str, Any]]:
    """按快照 ID 生成切片集合：chunk_id 在集合内唯一即可。"""
    return [{**chunk, "chunk_id": f"{record_id}:{chunk['seq']}"} for chunk in CHUNKS]


def _payload(
    *,
    request_id: str,
    publication_id: str,
    record_id: str,
    revision: int,
    base_record_id: str | None = None,
    content_hash: str | None = None,
    chunks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """构造 #03 / #05 正文（字段与手册 §六.03.1 / §六.03.3 一致）。"""
    payload: dict[str, Any] = {
        "request_id": request_id,
        "tenant_id": "gsi-default",
        "user_id": "42",
        "resource_type": "document",
        "resource_id": "doc-demo-001",
        "publication_id": publication_id,
        "record_id": record_id,
        "version_id": "1",
        "revision": revision,
        "knowledge_no": "KB-0000064",
        "metadata": {"title": "VPN申请操作手册", "trust": "A"},
        "content_hash": content_hash or canonical_content_hash(chunks or _chunks(record_id)),
        "chunks": chunks or _chunks(record_id),
    }
    if base_record_id is not None:
        payload["base_record_id"] = base_record_id
    return payload


def _delete_payload(*, request_id: str, publication_id: str, record_id: str, revision: int) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "tenant_id": "gsi-default",
        "user_id": "42",
        "resource_type": "document",
        "resource_id": "doc-demo-001",
        "publication_id": publication_id,
        "record_id": record_id,
        "version_id": "1",
        "revision": revision,
        "reason": "已审批废弃该知识切片集合",
    }


def _signed(method: str, path: str, body: bytes) -> dict[str, str]:
    """每次单发一个新 nonce：幂等回放用例不能撞上「nonce 重放」这层拒绝。"""
    return {
        "Content-Type": "application/json",
        **build_signed_headers(
            settings.CONTRACT_V1_SHARED_SECRET,
            client_id=settings.CONTRACT_V1_CLIENT_ID,
            method=method,
            path=path,
            body=body,
            nonce=f"kb-{uuid.uuid4().hex}",
        ),
    }


async def _call(method: str, path: str, payload: dict[str, Any] | None = None):
    body = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else b""
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
        return await client.request(method, path, content=body or None, headers=_signed(method, path, body))


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


async def _cleanup(publication_id: str, request_ids: list[str]) -> None:
    """清掉三张业务表与幂等台账，避免用例互相污染。"""
    async with async_session_factory() as session:
        await session.execute(delete(KbChunkModel).where(KbChunkModel.publication_id == publication_id))
        await session.execute(delete(KbRevisionModel).where(KbRevisionModel.publication_id == publication_id))
        await session.execute(delete(KbPublicationModel).where(KbPublicationModel.publication_id == publication_id))
        await session.execute(delete(SystemIdempotencyModel).where(SystemIdempotencyModel.biz_id.in_(request_ids)))
        await session.commit()


async def _chunk_rows(publication_id: str, record_id: str) -> list[KbChunkModel]:
    async with async_session_factory() as session:
        return list(
            (
                await session.execute(
                    select(KbChunkModel).where(
                        KbChunkModel.publication_id == publication_id,
                        KbChunkModel.record_id == record_id,
                    )
                )
            )
            .scalars()
            .all()
        )


async def _publication(publication_id: str) -> KbPublicationModel | None:
    async with async_session_factory() as session:
        return (
            await session.execute(select(KbPublicationModel).where(KbPublicationModel.publication_id == publication_id))
        ).scalar_one_or_none()


@requires_db
async def test_create_entry_persists_chunks_with_vectors() -> None:
    """#03 首发 201：三张表落库，向量条数 = 切片条数，publication 指针指向新快照。"""
    publication_id = f"pub-{uuid.uuid4().hex[:12]}"
    record_id = f"record-{uuid.uuid4().hex[:12]}"
    request_id = f"req-{uuid.uuid4().hex[:12]}"
    try:
        response = await _call(
            "POST", "/v1/knowledge/entries", _payload(
                request_id=request_id, publication_id=publication_id, record_id=record_id, revision=101
            )
        )
        assert response.status_code == 201, response.text
        receipt = response.json()
        assert receipt["request_id"] == request_id
        assert receipt["success"] is True
        assert receipt["accepted_chunk_count"] == len(CHUNKS)
        assert receipt["searchable"] is True
        assert receipt["content_hash"].startswith("sha256:")

        rows = await _chunk_rows(publication_id, record_id)
        assert len(rows) == len(CHUNKS)
        assert [row.seq for row in rows] == [1, 2, 3]
        assert all(row.embedding is not None and len(row.embedding) == settings.VECTOR_DIM for row in rows)
        assert all(row.status == "online" for row in rows)

        publication = await _publication(publication_id)
        assert publication is not None
        assert (publication.status, publication.current_record_id, publication.current_revision) == (
            "active",
            record_id,
            101,
        )
    finally:
        await _cleanup(publication_id, [request_id])


@requires_db
async def test_create_entry_replays_same_request_id() -> None:
    """同 request_id + 同正文重试回放首次 201 回执，不重复写库。"""
    publication_id = f"pub-{uuid.uuid4().hex[:12]}"
    record_id = f"record-{uuid.uuid4().hex[:12]}"
    request_id = f"req-{uuid.uuid4().hex[:12]}"
    payload = _payload(request_id=request_id, publication_id=publication_id, record_id=record_id, revision=101)
    try:
        first = await _call("POST", "/v1/knowledge/entries", payload)
        second = await _call("POST", "/v1/knowledge/entries", payload)
        assert first.status_code == 201
        assert second.status_code == 201
        assert second.json() == first.json()
        assert len(await _chunk_rows(publication_id, record_id)) == len(CHUNKS)
    finally:
        await _cleanup(publication_id, [request_id])


@requires_db
async def test_create_entry_conflicts_on_existing_publication() -> None:
    """同 publication_id 换 request_id 再发布 → 409 `STALE_REVISION`（Q7 口径：不隐式覆盖）。"""
    publication_id = f"pub-{uuid.uuid4().hex[:12]}"
    record_id = f"record-{uuid.uuid4().hex[:12]}"
    first_id = f"req-{uuid.uuid4().hex[:12]}"
    second_id = f"req-{uuid.uuid4().hex[:12]}"
    try:
        first = await _call(
            "POST", "/v1/knowledge/entries", _payload(
                request_id=first_id, publication_id=publication_id, record_id=record_id, revision=101
            )
        )
        assert first.status_code == 201

        second = await _call(
            "POST",
            "/v1/knowledge/entries",
            _payload(
                request_id=second_id,
                publication_id=publication_id,
                record_id=f"record-{uuid.uuid4().hex[:12]}",
                revision=102,
            ),
        )
        assert second.status_code == 409
        assert second.json()["error"]["code"] == contract_errors.STALE_REVISION
    finally:
        await _cleanup(publication_id, [first_id, second_id])


@requires_db
async def test_create_entry_rejects_content_hash_mismatch() -> None:
    """content_hash 与本次切片集合不符 → 409 `STALE_REVISION`，且不产生任何业务行。"""
    publication_id = f"pub-{uuid.uuid4().hex[:12]}"
    record_id = f"record-{uuid.uuid4().hex[:12]}"
    request_id = f"req-{uuid.uuid4().hex[:12]}"
    try:
        response = await _call(
            "POST",
            "/v1/knowledge/entries",
            _payload(
                request_id=request_id,
                publication_id=publication_id,
                record_id=record_id,
                revision=101,
                content_hash="sha256:0000000000000000000000000000000000000000000000000000000000000000",
            ),
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == contract_errors.STALE_REVISION
        assert await _publication(publication_id) is None
        assert await _chunk_rows(publication_id, record_id) == []
    finally:
        await _cleanup(publication_id, [request_id])


@requires_db
async def test_revise_entry_switches_pointer_and_keeps_old_snapshot() -> None:
    """#05 200：新快照整体写入，旧快照保留（#04 按 record_id 精读），指针切到新版本。"""
    publication_id = f"pub-{uuid.uuid4().hex[:12]}"
    first_record = f"record-{uuid.uuid4().hex[:12]}"
    second_record = f"record-{uuid.uuid4().hex[:12]}"
    first_id = f"req-{uuid.uuid4().hex[:12]}"
    second_id = f"req-{uuid.uuid4().hex[:12]}"
    try:
        assert (
            await _call(
                "POST",
                "/v1/knowledge/entries",
                _payload(
                    request_id=first_id, publication_id=publication_id, record_id=first_record, revision=101
                ),
            )
        ).status_code == 201

        revised = await _call(
            "PUT",
            f"/v1/knowledge/entries/{publication_id}",
            _payload(
                request_id=second_id,
                publication_id=publication_id,
                record_id=second_record,
                revision=102,
                base_record_id=first_record,
            ),
        )
        assert revised.status_code == 200, revised.text
        assert revised.json()["record_id"] == second_record
        assert revised.json()["accepted_chunk_count"] == len(CHUNKS)

        assert len(await _chunk_rows(publication_id, second_record)) == len(CHUNKS)
        assert len(await _chunk_rows(publication_id, first_record)) == len(CHUNKS)
        publication = await _publication(publication_id)
        assert publication is not None
        assert (publication.current_record_id, publication.current_revision, publication.status) == (
            second_record,
            102,
            "active",
        )
    finally:
        await _cleanup(publication_id, [first_id, second_id])


@requires_db
async def test_revise_entry_rejects_stale_base() -> None:
    """base_record_id 与当前基线不一致 → 409（旧事件迟到不能覆盖别人已发布的修订）。"""
    publication_id = f"pub-{uuid.uuid4().hex[:12]}"
    first_record = f"record-{uuid.uuid4().hex[:12]}"
    first_id = f"req-{uuid.uuid4().hex[:12]}"
    second_id = f"req-{uuid.uuid4().hex[:12]}"
    try:
        assert (
            await _call(
                "POST",
                "/v1/knowledge/entries",
                _payload(
                    request_id=first_id, publication_id=publication_id, record_id=first_record, revision=101
                ),
            )
        ).status_code == 201

        response = await _call(
            "PUT",
            f"/v1/knowledge/entries/{publication_id}",
            _payload(
                request_id=second_id,
                publication_id=publication_id,
                record_id=f"record-{uuid.uuid4().hex[:12]}",
                revision=102,
                base_record_id=f"record-{uuid.uuid4().hex[:12]}",
            ),
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == contract_errors.STALE_REVISION
    finally:
        await _cleanup(publication_id, [first_id, second_id])


@requires_db
async def test_revise_entry_requires_base_record_id() -> None:
    """#05 缺 base_record_id 属于契约必填缺失 → 400 `INVALID_REQUEST`（不能悄悄跳过并发校验）。"""
    publication_id = f"pub-{uuid.uuid4().hex[:12]}"
    record_id = f"record-{uuid.uuid4().hex[:12]}"
    request_id = f"req-{uuid.uuid4().hex[:12]}"
    try:
        response = await _call(
            "PUT",
            f"/v1/knowledge/entries/{publication_id}",
            _payload(
                request_id=request_id,
                publication_id=publication_id,
                record_id=record_id,
                revision=102,
            ),
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == contract_errors.INVALID_REQUEST
        assert "base_record_id" in response.json()["error"]["message"]
    finally:
        await _cleanup(publication_id, [request_id])


@requires_db
async def test_delete_entry_removes_chunks_and_blocks_revival() -> None:
    """#06 200：物理删切片（软删仍可被检索），publication 置 deleted，旧 revision 不能复活。"""
    publication_id = f"pub-{uuid.uuid4().hex[:12]}"
    record_id = f"record-{uuid.uuid4().hex[:12]}"
    create_id = f"req-{uuid.uuid4().hex[:12]}"
    delete_id = f"req-{uuid.uuid4().hex[:12]}"
    try:
        assert (
            await _call(
                "POST",
                "/v1/knowledge/entries",
                _payload(request_id=create_id, publication_id=publication_id, record_id=record_id, revision=101),
            )
        ).status_code == 201

        deleted = await _call(
            "DELETE",
            f"/v1/knowledge/entries/{publication_id}",
            _delete_payload(request_id=delete_id, publication_id=publication_id, record_id=record_id, revision=102),
        )
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["deleted"] is True
        assert deleted.json()["searchable"] is False

        assert await _chunk_rows(publication_id, record_id) == []
        publication = await _publication(publication_id)
        assert publication is not None
        assert (publication.status, publication.current_record_id, publication.current_revision) == (
            "deleted",
            None,
            102,
        )

        # 幂等台账留墓碑：expires_at 为空，永不清理（防「删除后重发同一 request_id」复活内容）
        async with async_session_factory() as session:
            row = (
                await session.execute(select(SystemIdempotencyModel).where(SystemIdempotencyModel.biz_id == delete_id))
            ).scalar_one()
            assert row.status == "tombstone"
            assert row.expires_at is None

        # 用旧 revision 重新修订 → 409（revision 单调 + 发布单元已删除）
        revive = await _call(
            "PUT",
            f"/v1/knowledge/entries/{publication_id}",
            _payload(
                request_id=f"req-{uuid.uuid4().hex[:12]}",
                publication_id=publication_id,
                record_id=f"record-{uuid.uuid4().hex[:12]}",
                revision=101,
                base_record_id=record_id,
            ),
        )
        assert revive.status_code == 409
        assert revive.json()["error"]["code"] == contract_errors.STALE_REVISION
    finally:
        await _cleanup(publication_id, [create_id, delete_id])


@requires_db
async def test_delete_entry_rejects_older_revision() -> None:
    """删除 revision 不高于已记录值 → 409（「删除请求与较新修订冲突」）。"""
    publication_id = f"pub-{uuid.uuid4().hex[:12]}"
    record_id = f"record-{uuid.uuid4().hex[:12]}"
    create_id = f"req-{uuid.uuid4().hex[:12]}"
    delete_id = f"req-{uuid.uuid4().hex[:12]}"
    try:
        assert (
            await _call(
                "POST",
                "/v1/knowledge/entries",
                _payload(request_id=create_id, publication_id=publication_id, record_id=record_id, revision=101),
            )
        ).status_code == 201

        response = await _call(
            "DELETE",
            f"/v1/knowledge/entries/{publication_id}",
            _delete_payload(request_id=delete_id, publication_id=publication_id, record_id=record_id, revision=101),
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == contract_errors.STALE_REVISION
        assert len(await _chunk_rows(publication_id, record_id)) == len(CHUNKS)
    finally:
        await _cleanup(publication_id, [create_id, delete_id])


@requires_db
async def test_embedding_failure_leaves_no_partial_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    """向量化失败 → 500 `INTERNAL_ERROR`，且不落任何业务行（不允许「有正文没向量」）。"""
    publication_id = f"pub-{uuid.uuid4().hex[:12]}"
    record_id = f"record-{uuid.uuid4().hex[:12]}"
    request_id = f"req-{uuid.uuid4().hex[:12]}"

    async def failing_embed_texts(self: EmbeddingClient, texts, *, batch_size: int | None = None):
        raise RuntimeError("embedding down")

    monkeypatch.setattr(EmbeddingClient, "embed_texts", failing_embed_texts)
    try:
        response = await _call(
            "POST",
            "/v1/knowledge/entries",
            _payload(request_id=request_id, publication_id=publication_id, record_id=record_id, revision=101),
        )
        assert response.status_code == 500
        assert response.json()["error"]["code"] == contract_errors.INTERNAL_ERROR
        assert await _publication(publication_id) is None
        assert await _chunk_rows(publication_id, record_id) == []
    finally:
        await _cleanup(publication_id, [request_id])


@requires_db
async def test_create_entry_validates_chunk_set_shape() -> None:
    """切片集合不合法（seq 不连续）→ 400 `INVALID_REQUEST`，校验发生在向量化之前。"""
    publication_id = f"pub-{uuid.uuid4().hex[:12]}"
    record_id = f"record-{uuid.uuid4().hex[:12]}"
    request_id = f"req-{uuid.uuid4().hex[:12]}"
    chunks = _chunks(record_id)
    chunks[1]["seq"] = 5
    try:
        response = await _call(
            "POST",
            "/v1/knowledge/entries",
            _payload(
                request_id=request_id,
                publication_id=publication_id,
                record_id=record_id,
                revision=101,
                chunks=chunks,
            ),
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == contract_errors.INVALID_REQUEST
        assert await _publication(publication_id) is None
    finally:
        await _cleanup(publication_id, [request_id])
