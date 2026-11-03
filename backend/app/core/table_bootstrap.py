"""表结构兜底建表（各业务域 bootstrap 共用）。

只做一件事：把域内声明过的表按「缺表才建」补齐。已存在的表一律跳过——不补列、不改类型、
不加索引；当前项目不使用迁移，结构变更需手工处理。

单表失败（如 pgvector 扩展缺失导致分片表建不出来）只告警，不牵连同域其它表。
"""

from sqlalchemy import inspect, text
from sqlalchemy.sql.schema import Table

from app.config.setting import settings
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


async def ensure_pg_trgm_extension() -> bool:
    """幂等创建 pg_trgm 扩展：关键词检索的中文 GIN 索引（``gin_trgm_ops``）依赖它。

    与 vector 扩展同口径：失败只告警、不阻断启动。缺扩展时检索仍正确，只是没有索引、
    退化为顺序扫描；托管库无建扩展权限时由 DBA 执行一次 ``CREATE EXTENSION pg_trgm``。
    """
    try:
        async with async_engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    except Exception as e:
        logger.warning("⚠️ pg_trgm 扩展初始化失败（关键词检索将退化为顺序扫描）: {}", e)
        return False
    return True


async def ensure_keyword_index() -> bool:
    """幂等补建分片表的中文关键词索引（T4.1，须在建表之后调用）。

    索引刻意**不写进模型**：``create_missing_tables`` 对已存在的表一律跳过，声明了也只对
    新库生效；而索引建不出来会把整张表的 DDL 一起带崩。放在启动期兜底，失败只降级检索性能。

    中文没有分词器，走 pg_trgm 的字符三元组：``ILIKE '%关键词%'`` 在关键词含 ≥3 个连续
    字符时可命中该索引，2 字词退化为顺序扫描（当前数据量可接受）。大表请关闭
    ``SEARCH_INDEX_AUTO_CREATE``，改用 ``sql/v1.6_keyword_search_index.sql`` 的
    ``CREATE INDEX CONCURRENTLY`` 人工建，避免启动期锁表。
    """
    if not settings.SEARCH_INDEX_AUTO_CREATE:
        return False
    try:
        async with async_engine.begin() as conn:
            await conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_sbp_ai_kb_chunk_content_trgm "
                    "ON sbp_ai_kb_chunk USING gin (content gin_trgm_ops)"
                )
            )
    except Exception as e:
        logger.warning("⚠️ 关键词 GIN 索引补建失败（检索将退化为顺序扫描）: {}", e)
        return False
    logger.info("==== 关键词检索索引就绪（sbp_ai_kb_chunk.content GIN / pg_trgm）====")
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
