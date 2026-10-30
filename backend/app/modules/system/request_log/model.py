"""请求结果台账模型（表 ``sbp_ai_system_request_log``）：#15 超时结果查询的承接方。

与幂等台账分工：幂等管"重试返回首次结果"，本表管"超时后查询最终结果"；
过期后查询返回 ``RESULT_EXPIRED`` 而不是 404。
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_model import MappedBase


class SystemRequestLogModel(MappedBase):
    """结果台账（表 sbp_ai_system_request_log）。"""

    __tablename__: str = "sbp_ai_system_request_log"
    __table_args__: tuple = (
        UniqueConstraint("request_id", name="uq_sbp_ai_system_request_log_request_id"),
        Index("ix_sbp_ai_system_request_log_status_created", "status", "created_time"),
        {"comment": "生成/写操作结果台账（超时结果查询）"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="主键ID")
    request_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="契约请求 ID")
    operation: Mapped[str] = mapped_column(String(20), nullable=False, comment="completion/mutation/delete")
    status: Mapped[str] = mapped_column(String(20), default="running", nullable=False, comment="running/succeeded/failed/expired")
    result_visible: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, comment="结果是否仍可返回")
    result_ref: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None, nullable=True, comment="结果引用")
    error_code: Mapped[str | None] = mapped_column(String(64), default=None, nullable=True, comment="失败码")
    created_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False, comment="创建时间"
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, comment="可查询期限（默认 24 小时）"
    )
