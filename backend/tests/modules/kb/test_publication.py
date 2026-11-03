"""发布单元（``sbp_ai_kb_publication``）：并发指针、归属校验、契约字段落点。

三件事都在保护同一个对象，所以放在一个文件里：

1. **CAS 指针推进**（P0-3）：``revise`` / ``delete`` 原本是「读 → 校验 → 写」，两步之间没锁，
   并发时后写的会把 revision 覆盖回去，单调性被破坏，墓碑防复活随之失效。这里用条件更新
   验证：期望版本被并发推进后，落败方 rowcount 必须是 0；
2. **归属三元组**（P0-10）：``_assert_scope`` 曾只比 ``resource_type``——拿到 publication_id
   就能改写他人知识。现在必须是 ``tenant_id + resource_type + resource_id`` 三元组；
3. **字段落点**（P0-10）：契约带来的 ``version_id / knowledge_no / metadata / chunk_id`` 必须
   真的写进库，否则 #04 精读只能靠自增 id 猜。

CAS 与纯单元用例用 sqlite 内存库（只建 publication 一张表，其它 kb 表带 vector 列跑不了）；
端到端用例打 ``@requires_db``，会写真库并自行清理。
"""

import uuid
from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config.setting import settings
from app.core.database import async_session_factory
from app.modules.kb.chunk.model import KbChunkModel
from app.modules.kb.publication.crud import KbPublicationCRUD
from app.modules.kb.publication.model import KbPublicationModel
from app.modules.kb.publication.schema import EntryChunkData, EntryWriteData
from app.modules.kb.publication.service import EntryScopeError, KbEntryService
from app.modules.kb.revision.model import KbRevisionModel

from tests.support import requires_db


@pytest.fixture
async def session() -> AsyncGenerator[AsyncSession, None]:
    """内存 sqlite + 只建 publication 表。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(KbPublicationModel.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        yield db
    await engine.dispose()


async def _seed(db: AsyncSession, *, publication_id: str = "pub-1", revision: int = 2) -> None:
    await db.execute(
        insert(KbPublicationModel).values(
            publication_id=publication_id,
            resource_type="document",
            current_record_id="record-1",
            current_revision=revision,
            status="active",
            # title 是 NOT NULL 且无库级默认：v1.4 合并 entry_meta 后由写入链路必填
            title=f"{publication_id} 测试条目标题",
        )
    )
    await db.commit()


def _publication(**overrides) -> KbPublicationModel:
    values = {
        "publication_id": "pub-scope",
        "resource_type": "document",
        "tenant_id": "tenant-a",
        "resource_id": "res-1",
        "current_revision": 1,
        "status": "active",
    }
    values.update(overrides)
    return KbPublicationModel(**values)


def _write_data(**overrides) -> EntryWriteData:
    values = {
        "request_id": "req-1",
        "tenant_id": "tenant-a",
        "user_id": "user-1",
        "resource_type": "document",
        "resource_id": "res-1",
        "publication_id": "pub-scope",
        "record_id": "record-1",
        "version_id": "ver-1",
        "revision": 2,
        "knowledge_no": "KB-0001",
        "content_hash": "sha256:" + "a" * 64,
        "metadata": {"source": "gsi"},
        "chunks": (
            EntryChunkData(chunk_id="chunk-1", seq=1, content="第一段"),
            EntryChunkData(chunk_id="chunk-2", seq=2, content="第二段"),
        ),
        "base_record_id": "record-0",
    }
    values.update(overrides)
    return EntryWriteData(**values)


# ── CAS 指针推进（P0-3） ────────────────────────────────────────────


async def test_advance_succeeds_on_expected_revision(session: AsyncSession) -> None:
    """期望版本与库里一致：更新命中，指针推进。"""
    await _seed(session, revision=2)
    crud = KbPublicationCRUD(session)

    ok = await crud.advance(
        publication_id="pub-1",
        expected_revision=2,
        values={"current_record_id": "record-2", "current_revision": 3},
    )
    await session.commit()

    assert ok is True
    publication = await crud.get(publication_id="pub-1")
    assert publication is not None
    assert publication.current_revision == 3
    assert publication.current_record_id == "record-2"


async def test_advance_loses_when_revision_was_moved(session: AsyncSession) -> None:
    """并发旧版本落败：rowcount=0，库里的指针保持胜者写入的值（不会被覆盖回去）。"""
    await _seed(session, revision=2)
    crud = KbPublicationCRUD(session)

    # 胜者：2 → 5
    assert await crud.advance(
        publication_id="pub-1",
        expected_revision=2,
        values={"current_record_id": "record-winner", "current_revision": 5},
    )
    await session.commit()

    # 落败方仍拿着旧基线 2 提交：必须失败
    stale = await crud.advance(
        publication_id="pub-1",
        expected_revision=2,
        values={"current_record_id": "record-loser", "current_revision": 3},
    )
    await session.commit()

    assert stale is False
    publication = await crud.get(publication_id="pub-1")
    assert publication is not None
    assert publication.current_revision == 5
    assert publication.current_record_id == "record-winner"


async def test_advance_returns_false_for_unknown_publication(session: AsyncSession) -> None:
    """发布单元不存在时同样 rowcount=0：调用方按「基线已变」处理，不会误判成功。"""
    crud = KbPublicationCRUD(session)

    assert await crud.advance(publication_id="missing", expected_revision=0, values={"current_revision": 1}) is False


# ── 归属校验（P0-10） ───────────────────────────────────────────────


def test_scope_rejects_cross_tenant_write() -> None:
    """核心越权场景：同一 resource_type、不同租户，过去能改，现在必须拒。"""
    with pytest.raises(EntryScopeError, match="租户"):
        KbEntryService._assert_scope(_publication(), _write_data(tenant_id="tenant-b"))


def test_scope_rejects_other_resource() -> None:
    """拿到别人的 publication_id 改另一份资源：resource_id 不一致必须拒。"""
    with pytest.raises(EntryScopeError, match="归属资源"):
        KbEntryService._assert_scope(_publication(), _write_data(resource_id="res-2"))


def test_scope_rejects_cross_type() -> None:
    """跨知识类型误用仍然是第一道拦截。"""
    with pytest.raises(EntryScopeError, match="属于 document"):
        KbEntryService._assert_scope(_publication(), _write_data(resource_type="faq"))


def test_scope_allows_matching_triplet() -> None:
    """三元组完全一致：放行（防止过度收紧把正常修订也拒掉）。"""
    KbEntryService._assert_scope(_publication(), _write_data())


def test_scope_degrades_for_legacy_rows() -> None:
    """迁移期前的行归属列为 NULL：只校验类型，不能让旧数据永久无法修订。"""
    legacy = _publication(tenant_id=None, resource_id=None)
    KbEntryService._assert_scope(legacy, _write_data(tenant_id="anyone", resource_id="anything"))


# ── 契约字段落点（P0-10） ───────────────────────────────────────────


async def test_chunk_rows_carry_contract_chunk_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """``chunk_id`` 必须进行字典：否则 #04 精读只能靠自增 id 猜。"""
    service = KbEntryService()

    async def fake_embed(chunks) -> list[list[float]]:
        return [[0.1] * settings.VECTOR_DIM for _ in chunks]

    monkeypatch.setattr(KbEntryService, "_embed", staticmethod(fake_embed))

    rows = await service._build_chunk_rows(_write_data())

    assert [row["chunk_id"] for row in rows] == ["chunk-1", "chunk-2"]
    assert [row["seq"] for row in rows] == [1, 2]


async def _cleanup(publication_id: str) -> None:
    async with async_session_factory() as session, session.begin():
        await session.execute(delete(KbChunkModel).where(KbChunkModel.publication_id == publication_id))
        await session.execute(delete(KbRevisionModel).where(KbRevisionModel.publication_id == publication_id))
        await session.execute(delete(KbPublicationModel).where(KbPublicationModel.publication_id == publication_id))


@requires_db
async def test_create_persists_contract_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    """首发之后：publication 有归属三元组，revision 有版本号/知识编号/元数据，chunk 有契约 chunk_id。"""
    service = KbEntryService()
    publication_id = f"pub-{uuid.uuid4().hex[:12]}"

    async def fake_embed(chunks) -> list[list[float]]:
        return [[0.1] * settings.VECTOR_DIM for _ in chunks]

    monkeypatch.setattr(KbEntryService, "_embed", staticmethod(fake_embed))

    data = _write_data(publication_id=publication_id, record_id=f"rec-{uuid.uuid4().hex[:8]}")
    try:
        receipt = await service.create(data)
        assert receipt.accepted_chunk_count == 2

        async with async_session_factory() as session:
            publication = (
                await session.execute(
                    select(KbPublicationModel).where(KbPublicationModel.publication_id == publication_id)
                )
            ).scalar_one()
            revision = (
                await session.execute(
                    select(KbRevisionModel).where(KbRevisionModel.publication_id == publication_id)
                )
            ).scalars().one()
            chunks = (
                await session.execute(
                    select(KbChunkModel).where(KbChunkModel.publication_id == publication_id).order_by(KbChunkModel.seq)
                )
            ).scalars().all()

        assert (publication.tenant_id, publication.resource_id) == (data.tenant_id, data.resource_id)
        assert revision.version_id == data.version_id
        assert revision.knowledge_no == data.knowledge_no
        assert revision.metadata_ == data.metadata
        assert [c.chunk_id for c in chunks] == ["chunk-1", "chunk-2"]
    finally:
        await _cleanup(publication_id)
