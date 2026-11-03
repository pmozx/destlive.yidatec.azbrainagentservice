"""kb 域启动引导：表结构与元数据（8 张，一表一目录）。

分片表含 pgvector 列与 HNSW 索引，调用方须保证 vector 扩展已就绪（见 ``app/bootstrap.py``）。

注：原 ``sbp_ai_kb_entry_meta`` 伴随表已合并入 ``sbp_ai_kb_publication``（v1.4，2026-11-03），
表数由 9 张降为 8 张；原 ``knowledge`` 子模块亦已合并入 ``publication``（v1.5，2026-11-03）。
"""

from sqlalchemy.sql.schema import Table

from app.core.table_bootstrap import create_missing_tables

from .models import (
    KbChatMessageModel,
    KbChatSessionModel,
    KbChunkModel,
    KbDupGroupModel,
    KbParseJobModel,
    KbParseResultModel,
    KbPublicationModel,
    KbRevisionModel,
)

# 依赖顺序：发布单元 → 版本记录 → 分片；解析任务 → 解析结果；会话 → 消息；重复分组
TABLES: tuple[Table, ...] = (
    KbPublicationModel.__table__,
    KbRevisionModel.__table__,
    KbChunkModel.__table__,
    KbParseJobModel.__table__,
    KbParseResultModel.__table__,
    KbDupGroupModel.__table__,
    KbChatSessionModel.__table__,
    KbChatMessageModel.__table__,
)


async def ensure_tables() -> list[str]:
    """幂等创建知识平台域 8 张表。"""
    return await create_missing_tables(TABLES, "kb")
