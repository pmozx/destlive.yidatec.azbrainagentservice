"""重复分组模型（表 ``sbp_ai_kb_dup_group``）：治理结论的物化层。

与只保留 30 分钟的 ``sbp_ai_kb_dup_result`` 分工不同：结果快照回答「这次检测看到了什么」，
本表回答「这条重复治理到什么程度」。所以键不能是易变的结果 ID，而是
``tenant_id + group_key``——``group_key`` 由「治理类型 + 排序后的成员身份」确定性推导（见
``dup_result.detector.build_group_key``），成员一变就是另一个分组，人工结论不会被张冠李戴。

``uuid`` 保留给管理端做行标识；契约里的 ``group_id`` 是 ``group_key``，两者语义不同不能互换。
"""

from decimal import Decimal
from typing import Any

from sqlalchemy import CheckConstraint, Index, Numeric, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_model import ModelMixin

from ..dup_result.schema import (
    KIND_CHUNK,
    KIND_DOC_EXACT,
    KIND_DOC_SIMILAR,
    KIND_FAQ_AI,
    KIND_FAQ_MANUAL,
    RESOURCE_DOCUMENT,
)

_KIND_VALUES = ", ".join(
    f"'{kind}'" for kind in (KIND_DOC_EXACT, KIND_DOC_SIMILAR, KIND_CHUNK, KIND_FAQ_AI, KIND_FAQ_MANUAL)
)


class KbDupGroupModel(ModelMixin):
    """重复分组（表 sbp_ai_kb_dup_group，#16–#20 治理物化）。"""

    __tablename__: str = "sbp_ai_kb_dup_group"
    __table_args__: tuple = (
        CheckConstraint("status IN ('active', 'merged', 'dismissed')", name="ck_sbp_ai_kb_dup_group_status"),
        CheckConstraint(f"kind IN ({_KIND_VALUES})", name="ck_sbp_ai_kb_dup_group_kind"),
        # 同一租户同一条重复只能有一个治理行：重复检测每次都跑，不能每次插新行
        Index("uq_sbp_ai_kb_dup_group_tenant_key", "tenant_id", "group_key", unique=True),
        Index("ix_sbp_ai_kb_dup_group_kind_status", "kind", "status"),
        Index("ix_sbp_ai_kb_dup_group_status_created", "status", "created_time"),
        {"comment": "重复分组（#16–#20 治理物化）"},
    )

    tenant_id: Mapped[str] = mapped_column(
        String(64), default="", server_default=text("''"), nullable=False, comment="租户 ID（分组只对本租户可见）"
    )
    group_key: Mapped[str] = mapped_column(
        String(64),
        default="",
        server_default=text("''"),
        nullable=False,
        comment="契约 group_id：类型 + 排序后成员身份的稳定摘要",
    )
    kind: Mapped[str] = mapped_column(
        String(20),
        default=KIND_DOC_SIMILAR,
        server_default=text(f"'{KIND_DOC_SIMILAR}'"),
        nullable=False,
        comment="治理分组类型：doc_exact/doc_similar/chunk/faq_ai/faq_manual",
    )
    title: Mapped[str] = mapped_column(
        String(255), default="", server_default=text("''"), nullable=False, comment="分组标题（建议主项标题）"
    )
    score_method: Mapped[str] = mapped_column(
        String(50), default="", server_default=text("''"), nullable=False, comment="得分方法版本（score_method）"
    )
    reason: Mapped[str] = mapped_column(
        String(500), default="", server_default=text("''"), nullable=False, comment="判断依据（人工可读）"
    )
    suggested_keep_id: Mapped[str | None] = mapped_column(
        String(64), default=None, nullable=True, comment="建议保留项的 hit_id"
    )
    result_id: Mapped[str | None] = mapped_column(
        String(64), default=None, nullable=True, comment="最近一次产出该分组的检测结果 result_id"
    )
    resource_type: Mapped[str] = mapped_column(
        String(20), default=RESOURCE_DOCUMENT, server_default=text(f"'{RESOURCE_DOCUMENT}'"), nullable=False, comment="资源类型"
    )
    scope: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None, nullable=True, comment="检测范围")
    members: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False, comment="组内条目")
    similarity: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), default=None, nullable=True, comment="组内最低相似度")
    merged_preview: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, default=None, nullable=True, comment="合并预览（草稿态，不落正式库）"
    )
    status: Mapped[str] = mapped_column(String(20), default="active", server_default=text("'active'"), nullable=False, comment="active/merged/dismissed")
