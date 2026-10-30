"""防重放台账模型（表 ``sbp_ai_system_nonce``）。

以 INSERT 争用 ``uq_..._client_nonce`` 判定首用，冲突即重放；不用"先查后插"，
否则并发重放会同时通过检查。跨实例共享、重启有效，取代 POC 的进程内字典。
"""

from datetime import UTC, datetime

from sqlalchemy import BigInteger, DateTime, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_model import MappedBase


class SystemNonceModel(MappedBase):
    """防重放台账（表 sbp_ai_system_nonce）：唯一约束是"首用"的唯一事实来源。"""

    __tablename__: str = "sbp_ai_system_nonce"
    __table_args__: tuple = (
        UniqueConstraint("client_id", "nonce", name="uq_sbp_ai_system_nonce_client_nonce"),
        Index("ix_sbp_ai_system_nonce_expires", "expires_at"),
        {"comment": "防重放 nonce 登记表（首用时间 + 过期清理）"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="主键ID")
    client_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="调用方标识")
    nonce: Mapped[str] = mapped_column(String(128), nullable=False, comment="nonce 原文（契约 16–128 位）")
    request_id: Mapped[str | None] = mapped_column(String(64), default=None, nullable=True, comment="关联请求（重放取证）")
    created_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False, comment="首用时间"
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, comment="过期时间（首用 + 时间窗，默认 10 分钟）"
    )
