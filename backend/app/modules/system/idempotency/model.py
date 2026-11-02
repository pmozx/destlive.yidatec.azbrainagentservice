"""幂等台账模型（表 ``sbp_ai_system_idempotency``）：三类幂等域共用。

``status`` 取值 ``processing / succeeded / failed / tombstone``；
墓碑（删除类操作）``expires_at`` 为 NULL，永不清理，用于拒绝"删除后复活"。
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Index, String, text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_model import MappedBase


class SystemIdempotencyModel(MappedBase):
    """幂等台账（表 sbp_ai_system_idempotency）：job / request / event 三类域共用。"""

    __tablename__: str = "sbp_ai_system_idempotency"
    __table_args__: tuple = (
        UniqueConstraint("scope", "biz_id", name="uq_sbp_ai_system_idempotency_scope_biz"),
        Index("ix_sbp_ai_system_idempotency_expires", "expires_at"),
        # 唯一键是 (scope, biz_id)，只按 biz_id 查（跨域排查同号）用不上它，补单列索引（P1-6）
        Index("ix_sbp_ai_system_idempotency_biz_id", "biz_id"),
        {"comment": "幂等台账（job / request / event 三类域）"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="主键ID")
    scope: Mapped[str] = mapped_column(String(32), nullable=False, comment="幂等域：job/request/event")
    biz_id: Mapped[str] = mapped_column(String(128), nullable=False, comment="幂等键（job_id/request_id/event_id）")
    body_hash: Mapped[str] = mapped_column(String(64), nullable=False, comment="请求体 SHA-256（异正文判 409）")
    status: Mapped[str] = mapped_column(String(20), default="processing", server_default=text("'processing'"), nullable=False, comment="台账状态")
    result_ref: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, default=None, nullable=True, comment="首次结果摘要（回放返回，不存正文全文）"
    )
    created_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), server_default=text("now()"), nullable=False, comment="创建时间"
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, nullable=True, comment="保留到期（NULL=墓碑，永不清理）"
    )
