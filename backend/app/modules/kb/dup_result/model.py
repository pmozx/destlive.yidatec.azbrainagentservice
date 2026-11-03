"""重复检测结果快照模型（表 ``sbp_ai_kb_dup_result``）：``uuid`` 即契约 ``result_id``。

为什么要有这张表（而不是每次翻页重算）：契约 §九 规定「结果保留 30 分钟并绑定租户、用户、
范围与策略」，翻页、详情、预览三步必须看到**同一份**检测结果；重算会因来源随时变化而出现
「第一页有、第二页没了」的自相矛盾。快照自包含 ``groups``，所以 D02/D03/D04 的分页与详情
不依赖当时的库状态，只依赖这张只读快照 + 重新校验的来源摘要。

``scope_digest`` 是范围/内容/策略的联合摘要：翻页时 GSI 会重新签发 scope（scope_id 与时间戳
都变了），只有排除这三个易变字段，才能判断「还是不是同一次治理」。
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_model import ModelMixin


class KbDupResultModel(ModelMixin):
    """重复检测只读结果快照（表 sbp_ai_kb_dup_result）。"""

    __tablename__: str = "sbp_ai_kb_dup_result"
    __table_args__: tuple = (
        CheckConstraint("total_groups >= 0", name="ck_sbp_ai_kb_dup_result_total_non_negative"),
        # 结果属于某个租户：管理端按租户排查与清理都走这个前缀
        Index("ix_sbp_ai_kb_dup_result_tenant_created", "tenant_id", "created_time"),
        # 过期清理（30 分钟）不能全表扫
        Index("ix_sbp_ai_kb_dup_result_expires", "expires_at"),
        {"comment": "重复检测只读结果快照（绑定租户/用户/范围摘要，保留 30 分钟）"},
    )

    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="租户 ID（结果只对本租户可见）")
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="发起检测的用户主键（字符串）")
    score_profile: Mapped[str] = mapped_column(String(50), nullable=False, comment="检测策略标识，如 gsi-duplicate-v1")
    kinds: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False, comment="本次治理类型（五类枚举）")
    scope: Mapped[dict | None] = mapped_column(JSONB, default=None, nullable=True, comment="授权范围快照（含 scope_id）")
    scope_digest: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="范围+内容+策略摘要（翻页校验，不含 scope_id 与时间戳）"
    )
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False, comment="内容摘要（审计与排障）")
    groups: Mapped[list[dict]] = mapped_column(JSONB, default=list, nullable=False, comment="全部分组（契约投影）")
    coverage: Mapped[dict | None] = mapped_column(JSONB, default=None, nullable=True, comment="覆盖度（requested/evaluated）")
    total_groups: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False, comment="该结果的全部分组数"
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, comment="结果到期时间（创建后 30 分钟）"
    )
