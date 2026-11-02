from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    create_async_engine,    # 异步创建数据库引擎
    AsyncEngine,            # 异步数据库引擎类
    async_sessionmaker,     # 异步会话工厂函数
    AsyncSession,           # 异步会话类
)
from app.config.setting import settings
from app.core.logger import logger
from app.common.enums import DatabaseTypeEnum


def create_async_engine_and_session(db_url: str = settings.async_db_url) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """获取异步数据库会话连接。

    参数:
    - db_url (str): 异步数据库 URL，默认取配置项 ASYNC_DB_URI。

    返回:
    - tuple[AsyncEngine, async_sessionmaker[AsyncSession]]: 异步数据库引擎和会话工厂。
    """
    try:
        # 异步数据库引擎
        if settings.DATABASE_TYPE == DatabaseTypeEnum.SQLITE.value:
            async_engine = create_async_engine(
                url=db_url,
                echo=settings.DATABASE_ECHO,
                echo_pool=settings.ECHO_POOL,
                pool_pre_ping=settings.POOL_PRE_PING,
                future=settings.FUTURE,
                pool_recycle=settings.POOL_RECYCLE,
            )
        else:
            async_engine = create_async_engine(
                url=db_url,
                echo=settings.DATABASE_ECHO,
                echo_pool=settings.ECHO_POOL,
                pool_pre_ping=settings.POOL_PRE_PING,
                future=settings.FUTURE,
                pool_recycle=settings.POOL_RECYCLE,
                pool_size=settings.POOL_SIZE,
                max_overflow=settings.MAX_OVERFLOW,
                pool_timeout=settings.POOL_TIMEOUT,
                pool_use_lifo=settings.POOL_USE_LIFO,
            )
    except Exception as e:
        logger.error(f"❌ 数据库连接失败 {e}")
        raise
    else:
        # 异步数据库会话工厂
        async_session_maker = async_sessionmaker[AsyncSession](
            bind=async_engine,
            autocommit=settings.AUTOCOMMIT,
            autoflush=settings.AUTOFLUSH,
            expire_on_commit=settings.EXPIRE_ON_COMMIT,
            class_=AsyncSession,
        )
        return async_engine, async_session_maker

async_engine, async_session_factory = create_async_engine_and_session()


# 数据库类型 → 版本查询 SQL 映射
_VERSION_SQL_MAP: dict[str, str] = {
    DatabaseTypeEnum.SQLITE.value: "SELECT sqlite_version()",
    DatabaseTypeEnum.MYSQL.value: "SELECT VERSION()",
    DatabaseTypeEnum.POSTGRES.value: "SELECT version()",
}


async def get_db_version(engine: AsyncEngine | None = None) -> str:
    """获取当前数据库版本信息。

    参数:
    - engine: 异步引擎，默认使用全局 async_engine。

    返回:
    - str: 数据库版本字符串。

    异常:
    - ValueError: 数据库类型不支持时抛出。
    """
    _engine = engine or async_engine
    sql = _VERSION_SQL_MAP.get(settings.DATABASE_TYPE)
    if sql is None:
        raise ValueError(f"不支持的数据库类型: {settings.DATABASE_TYPE}")

    async with _engine.connect() as conn:
        result = await conn.execute(text(sql))
        return result.fetchone()[0]