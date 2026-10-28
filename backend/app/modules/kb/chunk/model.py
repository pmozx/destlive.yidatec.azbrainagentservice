"""知识切块模型（表 sbp_ai_kb_chunk）：原文与向量同库存储，便于用一条 SQL 完成“过滤 + 召回”。

刻意不继承 ModelMixin：本表不使用软删除与操作人字段，Mixin 的 status 复合索引
会引用不存在的列（同 SystemApiLogModel 的处理方式）。
"""

from datetime import UTC, datetime
from typing import Any, Final

from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.config.setting import settings
from app.core.base_model import MappedBase

# 表名与索引名固定：DDL 走 f-string 拼接，不接受外部配置，避免标识符注入
KB_CHUNK_TABLE_NAME: Final[str] = "sbp_ai_kb_chunk"
KB_CHUNK_EMBEDDING_INDEX_NAME: Final[str] = "ix_sbp_ai_kb_chunk_embedding_hnsw"


class KbChunkModel(MappedBase):
    """知识切块表"""

    __tablename__: str = KB_CHUNK_TABLE_NAME
    __table_args__: tuple = (
        UniqueConstraint("space_id", "doc_id", "chunk_index", name="uq_sbp_ai_kb_chunk_doc_index"),
        Index("ix_sbp_ai_kb_chunk_space_doc", "space_id", "doc_id"),
        {"comment": "知识切块表（含 pgvector 向量）"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="主键ID")
    space_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True, comment="知识空间ID")
    doc_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True, comment="文档ID")
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False, comment="切块序号（同文档内从 0 递增）")
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="切块原文")
    page_no: Mapped[int | None] = mapped_column(Integer, default=None, nullable=True, comment="原文页码")
    meta: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, nullable=False, comment="业务附加信息（标题、来源、分类等）"
    )
    embedding: Mapped[list[float]] = mapped_column(
        Vector(settings.VECTOR_DIM), nullable=False, comment=f"向量（{settings.VECTOR_DIM} 维，cosine）"
    )
    created_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
        comment="创建时间",
    )
    updated_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
        comment="更新时间",
    )
