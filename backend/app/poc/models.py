"""POC 临时表模型（前缀 `poc_v1_`）。

为什么不用 `sbp_ai_` 正式命名空间、也不走 Alembic（计划 §七.7）：
1. T1.0 的 23 张表要按模块划分 + 公共字段 Mixin + 迁移基线，POC 只验证"数据放得下、查得出来"，
   抢占正式表名会让后续迁移凭空多出一批待清理对象；
2. POC 表允许推倒重建：解析任务、发布快照、切片向量、幂等回执、会话消息、阶段耗时各自独立，
   删表即回到干净状态，不影响 `/api/v1` 的业务表。

设计取舍：
- **不继承 ModelMixin**：POC 不需要 `uuid`/软删/操作人，切片与向量必须物理删除（否则"删了还能召回"）；
- **切片行冗余发布元数据**（resource_id/publication_id/revision/title/...）：检索只查一张表，
  省掉 join 发布表的成本，也贴合"AI 本地索引按快照对齐"的正式设计意图；
- `revision` 用整数存：契约要求同一发布单元单调递增（`stale_revision` 校验在 store 里做）。
"""

from datetime import UTC, datetime
from typing import Any, Final

from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.config.setting import settings
from app.core.base_model import MappedBase

PARSE_JOB_TABLE: Final[str] = "poc_v1_parse_job"
PUBLICATION_TABLE: Final[str] = "poc_v1_publication"
CHUNK_TABLE: Final[str] = "poc_v1_chunk"
IDEMPOTENCY_TABLE: Final[str] = "poc_v1_idempotency"
CHAT_MESSAGE_TABLE: Final[str] = "poc_v1_chat_message"
METRIC_TABLE: Final[str] = "poc_v1_metric"

CHUNK_EMBEDDING_INDEX: Final[str] = "ix_poc_v1_chunk_embedding_hnsw"


def _now() -> datetime:
    return datetime.now(UTC)


class PocParseJobModel(MappedBase):
    """解析任务（#01 受理 → #02 查询）：`result` 存契约 `result` 对象原文。"""

    __tablename__: str = PARSE_JOB_TABLE
    __table_args__: tuple = (
        UniqueConstraint("job_id", name="uq_poc_v1_parse_job_job_id"),
        {"comment": "POC-解析任务表（临时）"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True, comment="GSI 解析任务ID（幂等键）")
    body_hash: Mapped[str] = mapped_column(String(64), nullable=False, comment="首次受理正文字摘要（同 ID 异正文 409）")
    request_body: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False, comment="受理时的请求正文")
    status: Mapped[str] = mapped_column(String(16), default="queued", nullable=False, comment="queued/running/succeeded/failed")
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="进度百分比 0-100")
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None, nullable=True, comment="解析产物（契约 result）")
    error_code: Mapped[str | None] = mapped_column(String(64), default=None, nullable=True, comment="failed 时的机器错误码")
    error_message: Mapped[str | None] = mapped_column(Text, default=None, nullable=True, comment="failed 时的失败说明")
    created_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)


class PocPublicationModel(MappedBase):
    """已批准切片集合的首发布回执（#03）：一条发布单元一行。"""

    __tablename__: str = PUBLICATION_TABLE
    __table_args__: tuple = (
        UniqueConstraint("request_id", name="uq_poc_v1_publication_request_id"),
        UniqueConstraint("publication_id", name="uq_poc_v1_publication_publication_id"),
        {"comment": "POC-发布单元回执表（临时）"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True, comment="GSI 操作ID（幂等键）")
    body_hash: Mapped[str] = mapped_column(String(64), nullable=False, comment="正文字摘要（同 ID 异正文 409）")
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(16), nullable=False, comment="document/faq")
    resource_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="知识主记录ID")
    publication_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True, comment="发布单元ID")
    record_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="内容快照ID")
    version_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="业务版本标识（字符串比较）")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, comment="索引修订号（单调递增）")
    knowledge_no: Mapped[str] = mapped_column(String(64), nullable=False, comment="知识编号（仅展示）")
    title: Mapped[str] = mapped_column(String(512), default="", nullable=False, comment="知识标题")
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False, comment="完整切片集合摘要（GSI 计算）")
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="写入切片条数")
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False, comment="metadata 快照")
    created_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)


class PocChunkModel(MappedBase):
    """正式切片 + 向量（#03 写入 / #07 检索 / #14 引用）：原文与向量同库同表。"""

    __tablename__: str = CHUNK_TABLE
    __table_args__: tuple = (
        UniqueConstraint("publication_id", "chunk_id", name="uq_poc_v1_chunk_publication_chunk"),
        {"comment": "POC-切片向量表（临时）"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    publication_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True, comment="发布单元ID")
    record_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="内容快照ID")
    chunk_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True, comment="GSI 切片ID")
    seq: Mapped[int] = mapped_column(Integer, nullable=False, comment="切片顺序（从1开始）")
    heading: Mapped[str | None] = mapped_column(String(512), default=None, nullable=True, comment="切片小标题")
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="切片正文")
    page_from: Mapped[int | None] = mapped_column(Integer, default=None, nullable=True)
    page_to: Mapped[int | None] = mapped_column(Integer, default=None, nullable=True)
    # 冗余发布元数据：检索单表出结果，避免每次 join（见模块头部第 2 条取舍）
    resource_type: Mapped[str] = mapped_column(String(16), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    version_id: Mapped[str] = mapped_column(String(64), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    knowledge_no: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    embedding: Mapped[list[float]] = mapped_column(
        Vector(settings.VECTOR_DIM), nullable=False, comment=f"向量({settings.VECTOR_DIM}维, cosine)"
    )
    created_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class PocIdempotencyModel(MappedBase):
    """通用幂等回执（POC 版，T1.3 会换成三类台账）：同 key 同正文回原回执，异正文 409。"""

    __tablename__: str = IDEMPOTENCY_TABLE
    __table_args__: tuple = (
        UniqueConstraint("scope", "key", name="uq_poc_v1_idempotency_scope_key"),
        {"comment": "POC-幂等回执表（临时）"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    scope: Mapped[str] = mapped_column(String(32), nullable=False, comment="parse/entries/chat")
    key: Mapped[str] = mapped_column(String(128), nullable=False, index=True, comment="job_id 或 request_id")
    body_hash: Mapped[str] = mapped_column(String(64), nullable=False, comment="正文字摘要")
    response: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False, comment="首次回执原文")
    created_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class PocChatMessageModel(MappedBase):
    """会话消息（POC 只落库、不回灌历史；历史分页与可见性属 T5.3）。"""

    __tablename__: str = CHAT_MESSAGE_TABLE
    __table_args__: tuple = (
        UniqueConstraint("message_id", name="uq_poc_v1_chat_message_message_id"),
        {"comment": "POC-会话消息表（临时）"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True, comment="AI 会话ID")
    message_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="AI 消息ID")
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, comment="user/assistant")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True, comment="产生该消息的操作ID")
    created_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class PocMetricModel(MappedBase):
    """阶段耗时（T0.4"关键耗时初测"的数据源）：一次请求一行，阶段明细存 JSONB。"""

    __tablename__: str = METRIC_TABLE
    __table_args__: tuple = ({"comment": "POC-请求阶段耗时表（临时）"},)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, index=True, comment="parse/publish/search/chat")
    key: Mapped[str] = mapped_column(String(128), nullable=False, comment="job_id 或 request_id")
    total_ms: Mapped[int] = mapped_column(Integer, nullable=False, comment="端到端耗时（毫秒）")
    stages: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False, comment="各阶段耗时明细")
    created_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
