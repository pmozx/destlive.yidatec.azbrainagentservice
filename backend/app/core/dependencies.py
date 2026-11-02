from collections.abc import AsyncGenerator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction

from app.core.database import async_session_factory

async def db_getter(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """数据库会话 — 请求级生命周期管理：一个 HTTP 请求内所有 SQL 共享同一个事务。

    事务口径（不能改回 ``async with session.begin()``，原因如下）：

    1. FastAPI（>=0.106）的依赖退出代码在**响应发出之后**才执行，且路由处理函数抛出的异常
       **不会**抛进挂起在 yield 处的依赖生成器——异常直接向上传给路由类/全局异常处理器；
    2. 因此 ``ContractApiRoute``（app/modules_api/contract/errors.py）把异常换成错误响应后，依赖栈看到的
       是一次"正常"退出：若用 ``session.begin()`` 上下文，事务会被当成成功而**提交**，
       失败请求的半成品数据就会落库（优化报告 P0-1）；
    3. 所以此处显式管理事务：成功 → commit；异常抛进生成器 → rollback 后原样上抛；
       契约路由失败（``request.state.contract_failed`` 由 ``_failed()`` 在换错误响应前标记）
       → 提交前显式 rollback。

    读操作也走这个事务（牺牲一点 MVCC 隔离换取读已写一致性）；对只读请求 rollback 无副作用。
    """
    async with async_session_factory() as session:
        transaction: AsyncSessionTransaction = await session.begin()
        try:
            yield session
        except BaseException:
            # 后置依赖初始化失败 / 客户端断连等：异常会抛进生成器，回滚后原样上抛
            await transaction.rollback()
            raise
        if getattr(request.state, "contract_failed", False):
            # 契约路由类已把异常换成错误响应（无异常可抛到这里），
            # 但失败请求绝不能提交：把半成品整体回滚
            await transaction.rollback()
            return
        await transaction.commit()
