"""集成-知识库-分片模型（表 sbp_ai_integration_kb_chunk）：原文与向量同库存储，一条 SQL 完成「过滤 + 召回」。

刻意不继承 ModelMixin：分片是向量化中间产物，删除必须物理删除（留回收站会导致
「删了还能被检索到」的隐性故障），也不需要 uuid 与操作人字段。
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
INTEGRATION_KB_CHUNK_TABLE_NAME: Final[str] = "sbp_ai_integration_kb_chunk"
INTEGRATION_KB_CHUNK_EMBEDDING_INDEX_NAME: Final[str] = "ix_sbp_ai_integration_kb_chunk_embedding_hnsw"


class IntegrationKbChunkModel(MappedBase):
    """知识分片表（含 pgvector 向量）"""

    __tablename__: str = INTEGRATION_KB_CHUNK_TABLE_NAME
    __table_args__: tuple = (
        UniqueConstraint("space_id", "doc_id", "chunk_index", name="uq_sbp_ai_integration_kb_chunk_doc_index"),
        Index("ix_sbp_ai_integration_kb_chunk_space_doc", "space_id", "doc_id"),
        {"comment": "集成-知识库-分片表（含 pgvector 向量）"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="主键ID")
    space_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True, comment="知识空间ID")
    doc_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True, comment="文档ID")
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False, comment="分片序号(文档内从0递增)")
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="分片原文")
    page_no: Mapped[int | None] = mapped_column(Integer, default=None, nullable=True, comment="原文页码(引用溯源定位)")
    section_title: Mapped[str | None] = mapped_column(String(255), default=None, nullable=True, comment="所属标题链")
    token_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="估算字符数(分片长度口径)")
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False, comment="解析器附加信息")
    embedding: Mapped[list[float]] = mapped_column(
        Vector(settings.VECTOR_DIM), nullable=False, comment=f"向量({settings.VECTOR_DIM}维, cosine)"
    )
    created_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False, comment="创建时间"
    )
    updated_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
        comment="更新时间",
    )
