"""集成-AI 问答启动引导：建会话与消息两张表。

问答不依赖任何后台任务与调度器，启动期只做「建表」这一件事。
"""

from app.core.database import async_engine
from app.core.logger import logger

from .session.model import IntegrationChatMessageModel, IntegrationChatSessionModel

CHAT_TABLES = (
    IntegrationChatSessionModel.__table__,
    IntegrationChatMessageModel.__table__,
)


async def ensure_chat_tables() -> None:
    """幂等创建问答会话表与消息表。"""
    async with async_engine.begin() as conn:
        for table in CHAT_TABLES:
            await conn.run_sync(table.create, checkfirst=True)
    logger.info("==== AI 问答表就绪（{} 张）====", len(CHAT_TABLES))
