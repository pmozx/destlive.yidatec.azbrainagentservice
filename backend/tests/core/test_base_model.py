"""基座模型 / CRUD 的**结构性**口径回归（报告 P0-4 / A-1 / A-2）。

这一组断言的都是「删掉/不改就一直静默正确」的东西，肉眼看不出异常，只有断言能拦住：

1. **无冗余主键索引**（A-1）：``ModelMixin.id`` 曾带 ``index=True``，16 张表各多一条与主键
   完全重复的 ``ix_<table>_id``。优化器永远不会选中它，纯写放大；
2. **结构性列有 DDL 默认值**（A-2）：状态/时间/计数器只在 Python 侧有默认值，任何绕过 ORM
   的写入（裸 SQL、ETL、别的服务）都会撞非空约束。v1.7 因此 15 个测试同时失败，报的却是
   ``STALE_REVISION``——与真因相距极远；
3. **基座不做软删**（P0-4）：加回一个 ``is_deleted`` 看起来无害，但会立刻让 ``delete()``
   从物理删变成标记删，与「``kb_chunk`` 删除后必须不可召回」直接冲突。所以把"不存在"也钉住；
4. **同名索引不得重复声明**：``table.indexes`` 里是两个不同对象，建表时执行两次
   ``CREATE INDEX``，第二次才炸。
"""

import importlib
import pkgutil
from collections import Counter
from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.modules as pkg
from app.core.base_crud import CRUDBase
from app.core.base_model import MappedBase, ModelMixin
from app.core.base_schema import BaseSchema
from app.modules.kb.publication.crud import KbPublicationCRUD
from app.modules.kb.publication.model import KbPublicationModel

#: 必须带 DDL 默认值的结构性列。业务语义列（tenant_id 等）**不在此列**——
#: 给它们补 ``DEFAULT ''`` 会造出"看起来有值"的假归属数据（规范 D18）。
STRUCTURAL_COLUMNS = frozenset({"status", "created_time", "updated_time", "searchable"})

#: 基座曾经统一提供、后来整体移除的软删字段。加回任何一个都算回退。
SOFT_DELETE_COLUMNS = ("is_deleted", "deleted_time", "deleted_id")


def _all_tables():
    """导入全部模型模块后返回 metadata 里的表（按名字排序，保证报错信息稳定）。"""
    for module in pkgutil.walk_packages(pkg.__path__, pkg.__name__ + "."):
        if module.name.endswith((".model", ".models")):
            importlib.import_module(module.name)
    return list(MappedBase.metadata.sorted_tables)


@pytest.fixture
async def session() -> AsyncGenerator[AsyncSession, None]:
    """内存 sqlite + 只建 publication 表（kb 其它表带 vector 列，sqlite 建不了）。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(KbPublicationModel.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        yield db
    await engine.dispose()


# ── 索引口径（A-1） ──────────────────────────────────────────────────


def test_no_redundant_primary_key_index() -> None:
    """主键已有唯一索引，不得再声明 ``ix_<table>_id``（A-1）。"""
    tables = _all_tables()
    assert tables, "模型未被导入，metadata 为空"

    offenders = [
        f"{table.name}.{index.name}"
        for table in tables
        for index in table.indexes
        if index.name == f"ix_{table.name}_id"
    ]
    assert not offenders, f"出现与主键重复的索引（删掉 id 列的 index=True）：{offenders}"


def test_no_duplicate_index_names_per_table() -> None:
    """同一张表不得声明两条同名索引（建表时第二次 ``CREATE INDEX`` 必报 already exists）。"""
    dupes = {}
    for table in _all_tables():
        counts = Counter(index.name for index in table.indexes)
        bad = {name: n for name, n in counts.items() if n > 1}
        if bad:
            dupes[table.name] = bad
    assert not dupes, f"存在重复索引声明：{dupes}"


# ── 默认值口径（A-2） ───────────────────────────────────────────────


def test_structural_columns_have_server_default() -> None:
    """结构性列必须在 DDL 里有 DEFAULT（A-2），否则裸 SQL 插入必然失败。"""
    missing = [
        f"{table.name}.{column.name}"
        for table in _all_tables()
        for column in table.columns
        if column.name in STRUCTURAL_COLUMNS
        and column.server_default is None
        and not column.nullable  # 可空列本来就不需要
    ]
    assert not missing, f"结构性列缺 server_default：{missing}"


def test_model_mixin_timestamps_have_server_default() -> None:
    """基座的两个时间列覆盖 16 张表，单独钉住（非继承表各自声明，已由上一个用例覆盖）。"""
    assert issubclass(KbPublicationModel, ModelMixin), "取样表必须继承基座，否则测的不是基座字段"
    columns = KbPublicationModel.__table__.c
    assert columns["created_time"].server_default is not None
    assert columns["updated_time"].server_default is not None


# ── 删除语义（P0-4）：基座只做物理删 ────────────────────────────────


def test_model_mixin_has_no_soft_delete_columns() -> None:
    """基座模型不再提供软删字段：加回来就等于把删除语义重新变成两套。"""
    for name in SOFT_DELETE_COLUMNS:
        assert not hasattr(ModelMixin, name), f"ModelMixin 不应再有 {name}"


def test_base_schema_has_no_soft_delete_fields() -> None:
    """响应 DTO 也不该再对外暴露"是否已删除"——那一列已经不存在，留着只会永远返回 false。"""
    fields = BaseSchema.model_fields
    for name in ("is_deleted", "deleted_time"):
        assert name not in fields, f"BaseSchema 不应再有 {name}"


def test_crud_base_has_no_restore_or_clear() -> None:
    """``restore()`` 在字段消失后无从实现，``clear()`` 是无条件整表删除且零调用，一并移除。"""
    assert not hasattr(CRUDBase, "restore")
    assert not hasattr(CRUDBase, "clear")
    assert not hasattr(CRUDBase, "_supports_soft_delete")
    assert not hasattr(CRUDBase, "_soft_delete_values")


async def test_delete_removes_rows_physically(session: AsyncSession) -> None:
    """``delete()`` 必须是物理删：行真的没了，而不是打标记后继续占着唯一键。"""
    await session.execute(
        insert(KbPublicationModel).values(
            publication_id="pub-del",
            resource_type="document",
            current_record_id="record-1",
            current_revision=1,
            status="active",
            title="待删除的测试条目",
        )
    )
    await session.commit()

    crud = KbPublicationCRUD(session)
    target = await crud.get(publication_id="pub-del")
    assert target is not None

    await crud.delete(ids=[target.id])
    await session.commit()

    remaining = (await session.execute(select(func.count()).select_from(KbPublicationModel))).scalar_one()
    assert remaining == 0
    # 物理删之后同一个 publication_id 能重新发布；软删会在这里被唯一键挡住
    assert await crud.get(publication_id="pub-del") is None


def test_no_table_keeps_soft_delete_columns_or_indexes() -> None:
    """全量模型体检：所有业务表都不该再有软删列与 ``*_deleted`` 索引。"""
    tables = _all_tables()
    assert len(tables) >= 22, f"模型注册数量异常：{len(tables)}"

    dirty: list[str] = []
    for table in tables:
        for column in table.columns:
            if column.name in SOFT_DELETE_COLUMNS:
                dirty.append(f"{table.name}.{column.name}(列)")
        for index in table.indexes:
            if index.name and "deleted" in index.name:
                dirty.append(f"{table.name}.{index.name}(索引)")

    assert not dirty, "仍有软删残留：" + ", ".join(dirty)
