"""集成-AI 问答-会话与消息模型（表 sbp_ai_integration_chat_session / _message）。"""

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_model import ModelMixin

INTEGRATION_CHAT_SESSION_TABLE_NAME: str = "sbp_ai_integration_chat_session"
INTEGRATION_CHAT_MESSAGE_TABLE_NAME: str = "sbp_ai_integration_chat_message"


class IntegrationChatSessionModel(ModelMixin):
    """问答会话表

    没有归属人字段：本域不走 RBAC，会话就是「当前使用者的会话列表」。
    ``space_ids`` 只在 scope_type=2（指定空间）时有意义，范围校验在服务层按「启用空间」做。
    """

    __tablename__: str = INTEGRATION_CHAT_SESSION_TABLE_NAME
    # 显式声明索引：ModelMixin 的默认 __table_args__ 会引用 status 列，本表不带状态位
    __table_args__: tuple = (
        Index("ix_sbp_ai_integration_chat_session_last_message_time", "last_message_time"),
        Index("ix_sbp_ai_integration_chat_session_created_deleted", "created_time", "is_deleted"),
        {"comment": "集成-AI问答-会话表"},
    )

    title: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="会话标题（默认取首问前20字）")
    scope_type: Mapped[int] = mapped_column(Integer, default=1, nullable=False, comment="问答范围(1:全部空间 2:指定空间)")
    space_ids: Mapped[list | None] = mapped_column(JSON, nullable=True, comment="指定空间ID数组(scope_type=2)")
    message_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="消息数（冗余计数）")
    last_message_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="最近消息时间（会话列表排序）"
    )


class IntegrationChatMessageModel(ModelMixin):
    """问答消息表

    assistant 消息会把「思考链路 / 引用快照 / 问题拓展 / 用量」一并落库，
    历史回显与统计都从这里取，不需要回查知识库。
    """

    __tablename__: str = INTEGRATION_CHAT_MESSAGE_TABLE_NAME
    __table_args__: tuple = (
        Index("ix_sbp_ai_integration_chat_message_session_time", "session_id", "created_time"),
        Index("ix_sbp_ai_integration_chat_message_feedback", "feedback"),
        Index("ix_sbp_ai_integration_chat_message_created_deleted", "created_time", "is_deleted"),
        {"comment": "集成-AI问答-消息表"},
    )

    session_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey(f"{INTEGRATION_CHAT_SESSION_TABLE_NAME}.id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
        comment="所属会话",
    )
    role: Mapped[int] = mapped_column(Integer, nullable=False, comment="角色(1:用户 2:助手)")
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="正文（助手为 Markdown）")
    analysis: Mapped[dict | None] = mapped_column(
        JSON, nullable=True, comment="思考链路(optimized/keywords/context/retrieval,仅助手)"
    )
    cites: Mapped[list | None] = mapped_column(JSON, nullable=True, comment="引用快照[{docId,chunkId,doc,page,snippet,space,cat}]")
    follow_ups: Mapped[list | None] = mapped_column(JSON, nullable=True, comment="问题拓展数组")
    disclaimer: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="免责声明文案")
    feedback: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="答案反馈(1:有用 -1:无用 NULL:未评)")
    not_found: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, comment="拒答标记（未召回/低置信）")
    model_name: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="生成模型")
    tokens_in: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="输入 Token")
    tokens_out: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="输出 Token")
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="端到端耗时(毫秒)")
