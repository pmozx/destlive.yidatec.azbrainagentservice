"""会话消息模型（表 ``sbp_ai_kb_chat_message``）：``uuid`` 即契约 ``message_id``。"""

from typing import Any

from sqlalchemy import Index, String, text, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_model import ModelMixin


class KbChatMessageModel(ModelMixin):
    """会话消息（表 sbp_ai_kb_chat_message）。"""

    __tablename__: str = "sbp_ai_kb_chat_message"
    __table_args__: tuple = (
        Index("ix_sbp_ai_kb_chat_message_status_created", "status", "created_time"),
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
    status: Mapped[str] = mapped_column(String(20), default="active", server_default=text("'active'"), nullable=False, comment="active/hidden/deleted")
