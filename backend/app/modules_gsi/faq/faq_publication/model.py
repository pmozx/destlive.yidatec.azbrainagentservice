"""GSI 业务表模型 · 模块 ``faq`` · 业务 ``faq_publication``（``public.sbp_faq_publication``）。

FAQ 按团队生效关系：``team`` 存 ``group_code``，``record_id`` 指向当前生效快照；
``access_rules_json`` 是**生效**规则（优先于 record 快照），与编辑态受众表无关。
"""

from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.modules_gsi.base import SbpBase


class SbpFaqPublicationModel(SbpBase):
    """FAQ 按受众团队生效内容。"""

    __tablename__ = "sbp_faq_publication"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="主键")
    faq_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="FAQ 主键")
    team: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="获授权受众团队(group_code)"
    )
    record_id: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="当前生效的 FAQ 审批快照"
    )
    index_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
        comment="索引同步状态：pending/succeeded/failed",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, comment="更新时间"
    )
    access_rules_json: Mapped[str | None] = mapped_column(
        Text, nullable=True, default=None, comment="生效的有效期及不可见团队规则"
    )
