"""集成-AI 问答启动引导：会话与消息两张表。

问答不依赖任何后台任务与调度器，启动期只做「建表」这一件事。
"""

from sqlalchemy.sql.schema import Table

from app.core.table_bootstrap import create_missing_tables

from .session.model import IntegrationChatMessageModel, IntegrationChatSessionModel

TABLES: tuple[Table, ...] = (
    IntegrationChatSessionModel.__table__,
    IntegrationChatMessageModel.__table__,
)


async def ensure_tables() -> list[str]:
    """幂等创建问答会话表与消息表。"""
    return await create_missing_tables(TABLES, "integration_chat")
