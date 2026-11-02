"""db_getter 事务口径回归（优化报告 P0-1）。

背景：``ContractApiRoute`` 把异常换成错误响应后，FastAPI（>=0.106）的依赖退出代码在响应
发出之后才执行且异常不会抛进生成器——依赖栈看到的是一次"正常"退出。若
``db_getter`` 用 ``async with session.begin()``，失败请求的事务会被当成成功提交，
半成品数据落库。

修复后口径（本文件逐一验证）：
1. 正常退出 → commit；
2. ``request.state.contract_failed=True``（契约路由失败标记）→ 提交前显式 rollback；
3. 异常抛进生成器（后置依赖失败 / 客户端断连）→ rollback 且异常原样上抛。

本文件只依赖 ``app.core.dependencies`` 与 sqlite+aiosqlite，不依赖应用启动与外部服务。
"""

from collections.abc import AsyncIterator

import pytest
from fastapi import Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.dependencies import db_getter


class _Base(DeclarativeBase):
    pass


class _TmpRow(_Base):
    """验证用表：一行记录即可判别提交与回滚。"""

    __tablename__ = "tmp_db_getter_probe"

    id: Mapped[int] = mapped_column(primary_key=True)


@pytest.fixture()
async def session_factory(tmp_path, monkeypatch) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'probe.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(_Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    # db_getter 内部引用的是模块级全局工厂：替换为本文件的 sqlite 工厂，测试不碰真实库
    monkeypatch.setattr("app.core.dependencies.async_session_factory", factory)
    yield factory
    await engine.dispose()


def _fake_request(*, contract_failed: bool = False) -> Request:
    """最小 Request：只为携带 state（db_getter 只读 ``request.state.contract_failed``）。"""
    request = Request(scope={"type": "http", "state": {}})
    if contract_failed:
        request.state.contract_failed = True
    return request


async def _row_count(factory: async_sessionmaker[AsyncSession]) -> int:
    async with factory() as session:
        return (await session.execute(text(f"SELECT COUNT(*) FROM {_TmpRow.__tablename__}"))).scalar_one()


async def test_success_request_commits(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """正常退出：事务提交，写入生效。"""
    agen = db_getter(_fake_request())
    session = await agen.__anext__()
    session.add(_TmpRow(id=1))
    await session.flush()
    async for _ in agen:  # 触发依赖退出代码
        break
    assert await _row_count(session_factory) == 1


async def test_contract_failed_request_rolls_back(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """契约路由失败（contract_failed 标记）：写入被回滚，半成品不落库。"""
    agen = db_getter(_fake_request(contract_failed=True))
    session = await agen.__anext__()
    session.add(_TmpRow(id=1))
    await session.flush()
    async for _ in agen:
        break
    assert await _row_count(session_factory) == 0


async def test_exception_thrown_into_generator_rolls_back(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """异常抛进生成器（后置依赖失败 / 断连等）：回滚且异常原样上抛。"""
    agen = db_getter(_fake_request())
    session = await agen.__anext__()
    session.add(_TmpRow(id=1))
    await session.flush()

    class _Boom(RuntimeError):
        pass

    with pytest.raises(_Boom):
        await agen.athrow(_Boom)
    assert await _row_count(session_factory) == 0
