"""表结构兜底建表（各业务域 bootstrap 共用）。

只做一件事：把域内声明过的表按「缺表才建」补齐。已存在的表一律跳过——不补列、不改类型、
不加索引；当前项目不使用迁移，结构变更需手工处理。

单表失败（如 pgvector 扩展缺失导致分片表建不出来）只告警，不牵连同域其它表。
"""

from sqlalchemy import inspect, text
from sqlalchemy.sql.schema import Table

from app.core.database import async_engine
from app.core.logger import logger


async def ensure_pgvector_extension() -> bool:
    """幂等创建 pgvector 扩展：kb 分片表的 ``vector`` 列与 HNSW 索引依赖它。

    必须在各域建表之前调用；失败返回 False（调用方只告警，不阻断启动）——
    扩展未就绪时 ``create_missing_tables`` 会跳过该表而不是让整个域失败。
    """
    try:
        async with async_engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    except Exception as e:
        logger.error("❌ pgvector 扩展初始化失败（kb 分片表将无法创建，检索能力不可用）: {}", e)
        return False
    return True


async def create_missing_tables(tables: tuple[Table, ...], module: str) -> list[str]:
    """按声明顺序建出该域缺失的表，返回本次新建的表名列表（已存在的表不动）。"""
    async with async_engine.connect() as conn:
        existing: set[str] = await conn.run_sync(lambda sync_conn: set(inspect(sync_conn).get_table_names()))

    created: list[str] = []
    for table in tables:
        if table.name in existing:
            continue
        try:
            async with async_engine.begin() as conn:
                await conn.run_sync(table.create, checkfirst=True)
        except Exception as e:
            # 表结构依赖外部能力（如 pgvector）：建不出来只影响该表，不阻断同域其它表
            logger.warning("⚠️ {} 域建表失败 {}（该表缺失时相关功能不可用）: {}", module, table.name, e)
            continue
        created.append(table.name)

    logger.info("==== {} 域表结构就绪（共 {} 张，本次新建 {} 张）====", module, len(tables), len(created))
    return created
