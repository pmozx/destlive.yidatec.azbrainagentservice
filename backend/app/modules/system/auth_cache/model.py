"""授权缓存模型（表 ``sbp_ai_system_auth_cache``，P1 可选）：共享表查询结果的短 TTL 快照。

不承担降级职责：共享表不可用时直接 503（ADR-8），本表只减少重复计算。
"""

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_model import MappedBase


class SystemAuthCacheModel(MappedBase):
    """授权缓存（scope_id + user_id 快照）。"""

    __tablename__: str = "sbp_ai_system_auth_cache"
    __table_args__: tuple = (
        UniqueConstraint("scope_id", "user_id", name="uq_sbp_ai_system_auth_cache_scope_user"),
        Index("ix_sbp_ai_system_auth_cache_expires", "expires_at"),
        {"comment": "授权缓存（scope_id + user_id 快照，TTL ≤120 秒）"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="主键ID")
    scope_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="授权范围 ID（authorization.scope_id）")
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="用户（sbp_sys_user.id）")
    permission_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, nullable=False, comment="权限快照（部门集/团队集/可见集摘要）"
    )
    watermark: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, nullable=True, comment="共享表更新时间水位（未变则复用）"
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, comment="过期时间（≤120 秒）")
