"""AI 问答单轮指标（表 ``sbp_ai_kb_chat_turn_metric``）：一轮一行，只写不改。

为什么单独一张表，而不是塞进 ``sbp_ai_kb_chat_message``：

1. 消息表被 #10 / #15 逐条投影给 GSI，往里加耗时、token、门限会让它长出第二套语义；
2. 指标只用于聚合（按天/模型/模式看质量与成本），要能自由加列而不动契约投影；
3. 写入必须**失败即降级**：指标是观测数据，不能反过来把一次正常的问答搞失败。

与 LangSmith 的分工：trace 回答「这一轮发生了什么」（节点顺序、每步耗时、模型输入输出），
本表回答「这一周质量与成本怎么变化」（可 SQL 聚合、不依赖外部 SaaS），两者用 ``trace_id`` 对上。
"""

from datetime import UTC, datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_model import MappedBase


class KbChatTurnMetricModel(MappedBase):
    """单轮问答指标（表 sbp_ai_kb_chat_turn_metric）。"""

    __tablename__: str = "sbp_ai_kb_chat_turn_metric"
    __table_args__: tuple = (
        # 趋势图：按模式 + 时间聚合（sync/stream 的成本结构不同，混在一起看没有意义）
        Index("ix_sbp_ai_kb_chat_turn_metric_mode_created", "mode", "created_time"),
        # 单会话回看：某个用户投诉「这轮答得不对」时按会话串起来
        Index("ix_sbp_ai_kb_chat_turn_metric_session", "session_id"),
        {"comment": "AI 问答单轮指标（质量/成本/耗时；写入失败不影响问答）"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="主键ID")
    trace_id: Mapped[str | None] = mapped_column(
        String(64), default=None, nullable=True, comment="LangSmith trace id（未开启追踪为空）"
    )
    session_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="chat_session.uuid")
    assistant_message_id: Mapped[str | None] = mapped_column(
        String(64), default=None, nullable=True, comment="助手消息 uuid（落库成功才有）"
    )
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="租户")
    user_hash: Mapped[str] = mapped_column(String(32), nullable=False, comment="用户标识的哈希（不存账号原值）")
    mode: Mapped[str] = mapped_column(String(16), nullable=False, comment="sync/stream")
    model: Mapped[str] = mapped_column(String(100), default="", server_default=text("''"), nullable=False, comment="本轮使用的模型名快照")
    prompt_hash: Mapped[str | None] = mapped_column(
        String(16), default=None, nullable=True, comment="业务提示词指纹（只存哈希，换提示词后按它分组对比）"
    )
    evidence_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"), nullable=False, comment="进入上下文的证据条数")
    top_score: Mapped[float | None] = mapped_column(Float, default=None, nullable=True, comment="最高相关度（排序质量）")
    tokens_in: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"), nullable=False, comment="输入 token（非流式门面不返回用量，记 0）")
    tokens_out: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"), nullable=False, comment="输出 token")
    retrieve_ms: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"), nullable=False, comment="检索+回查耗时（毫秒）")
    first_token_ms: Mapped[int | None] = mapped_column(Integer, default=None, nullable=True, comment="首字耗时（毫秒，仅流式）")
    generate_ms: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"), nullable=False, comment="生成耗时（毫秒）")
    persist_ms: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"), nullable=False, comment="落库耗时（毫秒）")
    total_ms: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"), nullable=False, comment="整轮耗时（毫秒）")
    no_evidence: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"), nullable=False, comment="是否因无证据拒答")
    finish_reason: Mapped[str | None] = mapped_column(String(32), default=None, nullable=True, comment="stop/no_evidence（失败轮看 error_code）")
    invalid_citations: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"), nullable=False, comment="被摘除的越界引用编号个数")
    error_code: Mapped[str | None] = mapped_column(String(64), default=None, nullable=True, comment="失败码（成功轮为空）")
    created_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=text("now()"),
        nullable=False,
        comment="创建时间",
        index=True,
    )
