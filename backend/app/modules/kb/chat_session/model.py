"""问答会话模型（表 ``sbp_ai_kb_chat_session``）：``uuid`` 即契约 ``session_id``，归属按签名身份校验。"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_model import ModelMixin


class KbChatSessionModel(ModelMixin):
    """问答会话（表 sbp_ai_kb_chat_session，#08–#13 六操作）。"""

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
