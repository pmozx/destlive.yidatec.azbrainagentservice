"""测试公共装置：模块之间隔离数据库连接池。

pytest.ini 里 `asyncio_default_test_loop_scope = module` 让同一模块的用例共用一个事件循环，
但 `app.core.database.async_engine` 是进程级单例，连接池中的 asyncpg 连接绑定在创建它的循环上。
上一个模块的循环关闭后，下一个模块复用这些残留连接会报 "Event loop is closed"，随后写库被
`_write_api_log_async` 吞掉异常（只记日志），查找侧就变成 NoResultFound 之类的“神秘”失败。

每个模块结束时显式 dispose 连接池：下一模块取连接时自然新建，不跨循环复用。
"""

import pytest

from app.core.database import async_engine


@pytest.fixture(scope="module", autouse=True)
async def _reset_db_pool_between_modules():
    """模块级收尾：在模块自己的事件循环内关闭池中的连接，避免跨模块踩已关闭的循环。"""
    yield
    await async_engine.dispose()
