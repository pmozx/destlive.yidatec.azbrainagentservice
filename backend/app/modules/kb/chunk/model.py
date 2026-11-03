"""知识切片模型（表 ``sbp_ai_kb_chunk``）★ 核心表：``uuid`` 即契约 ``chunk_id``。

只建 HNSW 向量索引；中文关键词索引（pg_trgm 的 ``gin(content gin_trgm_ops)``）刻意不在此声明：
``create_missing_tables`` 对已存在的表一律跳过，声明了也只对新库生效，而且索引建不出来会把整张表
的 DDL 一起带崩。改由 ``app.core.table_bootstrap.ensure_keyword_index()`` 启动期幂等补建，
存量库走 ``sql/v1.6_keyword_search_index.sql``（``CONCURRENTLY`` 人工建）。
"""

from sqlalchemy import BigInteger, CheckConstraint, Index, Integer, String, text, Text, UniqueConstraint
from pgvector.sqlalchemy import Vector
from sqlalchemy.orm import Mapped, mapped_column

from app.config.setting import settings
from app.core.base_model import ModelMixin


class KbChunkModel(ModelMixin):
    """知识切片（表 sbp_ai_kb_chunk，含 pgvector 向量）。"""

    __tablename__: str = "sbp_ai_kb_chunk"
    __table_args__: tuple = (
        UniqueConstraint("record_id", "seq", name="uq_sbp_ai_kb_chunk_rec_seq"),
        # 契约 #04 按 chunk_id 精读：同一快照内必须唯一，跨快照（record_id 不同）允许重复编号。
        # 迁移期前的行 chunk_id 为 NULL，PG/SQLite 的唯一约束都不拦多个 NULL，不影响存量。
        UniqueConstraint("record_id", "chunk_id", name="uq_sbp_ai_kb_chunk_rec_chunk"),
        CheckConstraint("status IN ('online', 'archived')", name="ck_sbp_ai_kb_chunk_status"),
        CheckConstraint("seq >= 0", name="ck_sbp_ai_kb_chunk_seq_non_negative"),
        CheckConstraint("revision >= 0", name="ck_sbp_ai_kb_chunk_revision_non_negative"),
        Index("ix_sbp_ai_kb_chunk_status_created", "status", "created_time"),
        Index("ix_sbp_ai_kb_chunk_pub_rev", "publication_id", "revision"),
        Index(
            "ix_sbp_ai_kb_chunk_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        {"comment": "知识切片（含 pgvector 向量，检索主战场）"},
    )

    publication_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="发布单元")
    record_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="版本记录")
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="版本号")
    chunk_id: Mapped[str | None] = mapped_column(
        String(64),
        default=None,
        nullable=True,
        comment="GSI 分片 ID（契约 chunk_id 原值，检索回显用；存量行 NULL）",
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False, comment="切片序号")
    heading: Mapped[str | None] = mapped_column(String(500), default=None, nullable=True, comment="所属标题路径")
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="切片正文（引用原文来源）")
    page_from: Mapped[int | None] = mapped_column(Integer, default=None, nullable=True, comment="起始页")
    page_to: Mapped[int | None] = mapped_column(Integer, default=None, nullable=True, comment="结束页")
    source_type: Mapped[str | None] = mapped_column(String(20), default=None, nullable=True, comment="切片来源类型")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, comment="切片指纹")
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(settings.VECTOR_DIM), default=None, nullable=True, comment=f"向量({settings.VECTOR_DIM}维, cosine)"
    )
    status: Mapped[str] = mapped_column(String(20), default="online", server_default=text("'online'"), nullable=False, comment="online/archived")
