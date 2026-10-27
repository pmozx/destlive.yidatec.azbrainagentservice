from dataclasses import dataclass
from typing import Any, Dict, AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_factory

"""
依赖注入介绍：依赖注入是FastAPI中一个非常重要的概念，它允许我们重用代码，并使代码更易于测试。
依赖注入通过依赖项来实现，依赖项是可重用的代码块，可以被多个路由处理函数调用。
依赖项可以返回任何内容，包括数据、对象、甚至是其他依赖项。
"""
async def common_parameters(
        q: str | None = None,
        skip: int = 0,
        limit: int = 10
) -> Dict[str, Any]:
    """
    基于函数的依赖项：公共参数依赖项函数：用于获取公共参数
    :param q: 查询参数
    :param skip: 跳过数量
    :param limit: 限制数量
    :return:
    """
    return {
        "q": q,
        "skip": skip,
        "limit": limit
    }


@dataclass
class CommonParameters:
    """
    基于类的依赖项：公共参数依赖项类：用于获取公共参数
    """
    q: str | None = None
    skip: int = 0
    limit: int = 10


async def db_getter() -> AsyncGenerator[AsyncSession, None]:
    """
    基于异步 + 生成器的依赖项：数据库会话 — 请求级生命周期管理。

    一个 HTTP 请求内所有 SQL 共享同一个事务：要么全成功，要么全失败。
    读操作也走这个事务（牺牲一点 MVCC 隔离换取读已写一致性）。
    """
    async with async_session_factory() as session, session.begin():
        yield session
