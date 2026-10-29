"""Alembic 环境（异步 asyncpg）：迁移与应用共用同一份配置，只管理本期 `sbp_ai_` 正式表。

三条纪律（对应设计文档 §3.4 Flyway/Alembic 分治）：
1. 连接串取自 `app.config.setting.settings`，不在 ini 里重复；
2. `include_object` 只放行 `sbp_ai_system_*` / `sbp_ai_kb_*` / `sbp_ai_voice_*`：
   既有的 `sbp_ai_integration_*` 过渡表与基座管理端表由各自 `create_all` 维护，不能进 AI 迁移；
3. `compare_type=True`：类型漂移（如 varchar(64)→(128)）必须被 autogenerate 发现，否则线上会被静默截断。
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

from app.config.setting import settings
from app.core.base_model import MappedBase

# 导入即注册到 MappedBase.metadata：少了任何一行，autogenerate 只会"看不见表"
import app.modules.kb.models  # noqa: F401
import app.modules.system.models  # noqa: F401
import app.modules.voice.models  # noqa: F401
from app.modules.system.api_log import model as api_log_model  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 注意：不把连接串写回 ini（``config.set_main_option``）：密码经 URL 编码后含 `%40`，
# configparser 会把它当插值语法直接报 ValueError；引擎直接由 settings 构造。
target_metadata = MappedBase.metadata

# 本期 Alembic 只负责的三大正式域；integration 过渡域与基座表不在此列
MANAGED_TABLE_PREFIXES: tuple[str, ...] = ("sbp_ai_system_", "sbp_ai_kb_", "sbp_ai_voice_")


def include_object(object_, name, type_, reflected, compare_to) -> bool:
    """只比对本期正式域对象：其余表（GSI 共享表、基座表、integration 过渡表）一律放行不做 DDL。"""
    if type_ == "table":
        return bool(name) and name.startswith(MANAGED_TABLE_PREFIXES)
    return True


def _configure(connection=None) -> None:
    context.configure(
        connection=connection,
        url=settings.async_db_url,
        target_metadata=target_metadata,
        include_object=include_object,
        compare_type=True,
        compare_server_default=True,
        # 索引/约束命名交给模型里的显式名字，禁掉自动命名模板，避免与库内既有名字冲突
        render_as_batch=False,
    )


def run_migrations_offline() -> None:
    """离线模式：输出 SQL 供 DBA 审核（`alembic upgrade head --sql`）。"""
    _configure()
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    _configure(connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = create_async_engine(settings.async_db_url, poolclass=pool.NullPool)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
