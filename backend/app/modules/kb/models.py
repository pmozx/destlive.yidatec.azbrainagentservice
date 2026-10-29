"""kb 模块表模型（P1 / T1.0）：解析、发布与版本、切片、会话消息、重复分组。

8 张表对应设计文档 §六；命名 `sbp_ai_kb_*`，与过渡域 `sbp_ai_integration_kb_*` 不共享任何对象。

三个建模取舍：
1. `parse_result` 不继承 `ModelMixin`：解析产物不可变、只按 90 天清理，软删与 uuid 无意义，
   契约里的 `job_id` 就是 `parse_job.uuid`；
2. `chunk` 只建 HNSW 向量索引，中文全文/trigram 索引留到 T4.1（keyword 检索）用原生 SQL 迁移补：
   表达式索引（`to_tsvector('simple', content)`）由 Alembic autogenerate 表达不准确，手写脚本更可控；
3. `publication` / `revision` / `chunk` 不存 GSI 业务字段（受众、部门），权限检索期实时算（ADR-8）。
"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.config.setting import settings
from app.core.base_model import MappedBase, ModelMixin

# 解析任务四态（契约 #01 202 / #02 200）
PARSE_JOB_STATUSES: tuple[str, ...] = ("queued", "running", "succeeded", "failed")


class KbParseJobModel(ModelMixin):
    """解析任务（表 sbp_ai_kb_parse_job）：`uuid` 即契约 `job_id`。"""

    __tablename__: str = "sbp_ai_kb_parse_job"
    __table_args__: tuple = (
        Index("ix_sbp_ai_kb_parse_job_status_deleted", "status", "is_deleted"),
        Index("ix_sbp_ai_kb_parse_job_created_deleted", "created_time", "is_deleted"),
        Index("ix_sbp_ai_kb_parse_job_status_created", "status", "created_time"),
        {"comment": "解析任务（#01/#02 状态机载体）"},
    )

    resource_type: Mapped[str] = mapped_column(String(20), nullable=False, comment="document/faq")
    file_meta: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False, comment="原始 metadata（来源/URL/页数）")
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="进度 0–100")
    error_code: Mapped[str | None] = mapped_column(String(64), default=None, nullable=True, comment="任务态失败码")
    error_message: Mapped[str | None] = mapped_column(Text, default=None, nullable=True, comment="任务态失败说明")
    status: Mapped[str] = mapped_column(String(20), default="queued", nullable=False, comment="queued/running/succeeded/failed")


class KbParseResultModel(MappedBase):
    """解析产物（表 sbp_ai_kb_parse_result）：#02 返回的完整结果，1:1 绑任务、不可变。"""

    __tablename__: str = "sbp_ai_kb_parse_result"
    __table_args__: tuple = (
        UniqueConstraint("job_id", name="uq_sbp_ai_kb_parse_result_job"),
        {"comment": "解析产物（chunks/suggested/warnings，无向量）"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="主键ID")
    job_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="parse_job.uuid")
    chunks: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False, comment="切片数组（无向量）")
    suggested: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None, nullable=True, comment="建议元数据")
    warnings: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False, comment="解析告警")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, comment="产物整体哈希（版本比对）")
    created_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False, comment="创建时间"
    )


class KbPublicationModel(ModelMixin):
    """发布单元（表 sbp_ai_kb_publication）：GSI 发布指针的镜像与生效状态，**不存内容**。"""

    __tablename__: str = "sbp_ai_kb_publication"
    __table_args__: tuple = (
        UniqueConstraint("publication_id", name="uq_sbp_ai_kb_publication_pid"),
        Index("ix_sbp_ai_kb_publication_status_deleted", "status", "is_deleted"),
        Index("ix_sbp_ai_kb_publication_created_deleted", "created_time", "is_deleted"),
        {"comment": "发布单元（检索版本容器）"},
    )

    publication_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="GSI sbp_km_doc_publication.id")
    resource_type: Mapped[str] = mapped_column(String(20), nullable=False, comment="document/faq")
    current_record_id: Mapped[str | None] = mapped_column(
        String(64), default=None, nullable=True, comment="当前生效记录（GSI 指针）"
    )
    current_revision: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False, comment="当前生效版本号（单调递增）")
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, nullable=True, comment="指针同步时间"
    )
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False, comment="active/inactive/deleted")


class KbRevisionModel(ModelMixin):
    """版本历史（表 sbp_ai_kb_revision）：`(publication_id, revision)` 唯一 + `base_record_id` 校验防并发覆盖。"""

    __tablename__: str = "sbp_ai_kb_revision"
    __table_args__: tuple = (
        UniqueConstraint("publication_id", "revision", name="uq_sbp_ai_kb_revision_pub_rev"),
        Index("ix_sbp_ai_kb_revision_status_deleted", "status", "is_deleted"),
        Index("ix_sbp_ai_kb_revision_created_deleted", "created_time", "is_deleted"),
        Index("ix_sbp_ai_kb_revision_record", "record_id"),
        {"comment": "发布单元版本快照"},
    )

    publication_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="归属发布单元")
    record_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="本版本内容记录（sbp_km_doc_record.id）")
    base_record_id: Mapped[str | None] = mapped_column(String(64), default=None, nullable=True, comment="基线记录（防覆盖）")
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="版本号")
    content_hash: Mapped[str | None] = mapped_column(String(64), default=None, nullable=True, comment="内容指纹")
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="切片数")
    searchable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, comment="是否已可检索")
    status: Mapped[str] = mapped_column(String(20), default="online", nullable=False, comment="online/archived")


class KbChunkModel(ModelMixin):
    """切片与向量（表 sbp_ai_kb_chunk）★ 核心表：`uuid` 即契约 `chunk_id`。"""

    __tablename__: str = "sbp_ai_kb_chunk"
    __table_args__: tuple = (
        UniqueConstraint("record_id", "seq", name="uq_sbp_ai_kb_chunk_rec_seq"),
        Index("ix_sbp_ai_kb_chunk_status_deleted", "status", "is_deleted"),
        Index("ix_sbp_ai_kb_chunk_created_deleted", "created_time", "is_deleted"),
        Index("ix_sbp_ai_kb_chunk_pub_rev_deleted", "publication_id", "revision", "is_deleted"),
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
    status: Mapped[str] = mapped_column(String(20), default="online", nullable=False, comment="online/archived")


class KbChatSessionModel(ModelMixin):
    """会话（表 sbp_ai_kb_chat_session）：`uuid` 即契约 `session_id`，归属按签名身份校验。"""

    __tablename__: str = "sbp_ai_kb_chat_session"
    __table_args__: tuple = (
        Index("ix_sbp_ai_kb_chat_session_status_deleted", "status", "is_deleted"),
        Index("ix_sbp_ai_kb_chat_session_created_deleted", "created_time", "is_deleted"),
        Index("ix_sbp_ai_kb_chat_session_user_active", "user_id", "last_active_at"),
        {"comment": "问答会话（#08–#13 六操作）"},
    )

    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="归属用户（sbp_sys_user.id）")
    title: Mapped[str] = mapped_column(String(200), default="", nullable=False, comment="会话标题")
    message_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="消息数")
    last_active_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, nullable=True, comment="最近活跃（排序）"
    )
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False, comment="active/cleared/deleted")


class KbChatMessageModel(ModelMixin):
    """消息（表 sbp_ai_kb_chat_message）：`uuid` 即契约 `message_id`。"""

    __tablename__: str = "sbp_ai_kb_chat_message"
    __table_args__: tuple = (
        Index("ix_sbp_ai_kb_chat_message_status_deleted", "status", "is_deleted"),
        Index("ix_sbp_ai_kb_chat_message_created_deleted", "created_time", "is_deleted"),
        Index("ix_sbp_ai_kb_chat_message_session_created", "session_id", "created_time"),
        {"comment": "会话消息与引用留痕"},
    )

    session_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="归属会话（chat_session.uuid）")
    role: Mapped[str] = mapped_column(String(16), nullable=False, comment="user/assistant")
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="消息正文")
    citations: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False, comment="引用六元组数组")
    knowledge_deps: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, nullable=False, comment="依赖的 publication_id + revision（失效则整条隐藏）"
    )
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False, comment="active/hidden/deleted")


class KbDupGroupModel(ModelMixin):
    """重复分组（表 sbp_ai_kb_dup_group）：`uuid` 即契约 `group_id`，合并预览只暂存本表。"""

    __tablename__: str = "sbp_ai_kb_dup_group"
    __table_args__: tuple = (
        Index("ix_sbp_ai_kb_dup_group_status_deleted", "status", "is_deleted"),
        Index("ix_sbp_ai_kb_dup_group_created_deleted", "created_time", "is_deleted"),
        Index("ix_sbp_ai_kb_dup_group_status_created", "status", "created_time"),
        {"comment": "重复分组（#16–#20 治理物化）"},
    )

    resource_type: Mapped[str] = mapped_column(String(20), nullable=False, comment="资源类型")
    scope: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None, nullable=True, comment="检测范围")
    members: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False, comment="组内条目")
    similarity: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), default=None, nullable=True, comment="组内最高相似度")
    merged_preview: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, default=None, nullable=True, comment="合并预览（草稿态，不落正式库）"
    )
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False, comment="active/merged/dismissed")
