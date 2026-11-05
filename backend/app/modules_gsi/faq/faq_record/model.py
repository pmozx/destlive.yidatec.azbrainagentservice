"""GSI 业务表模型 · 模块 ``faq`` · 业务 ``faq_record``（``public.sbp_faq_record``）。

FAQ 审批快照：``stage`` 是四阶段（version / owner / audience / owner_final），
与知识侧 ``save / owner / audience`` 三阶段不同，展示时不能照抄知识页口径。
"""

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.modules_gsi.base import SbpBase


class SbpFaqRecordModel(SbpBase):
    """FAQ 审批快照（限字段只读视图）。"""

    __tablename__ = "sbp_faq_record"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="主键")
    faq_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="FAQ 主键")
    stage: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="阶段：version保存内容 owner内容审 audience受众审 owner_final终审发布",
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="状态：saved/pending/approved/returned/cancelled",
    )
    snapshot_json: Mapped[str | None] = mapped_column(
        Text, nullable=True, default=None, comment="审批快照 JSON"
    )
