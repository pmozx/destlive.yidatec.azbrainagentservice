"""GSI 业务表模型 · 模块 ``km`` · 业务 ``doc_record``（``public.sbp_km_doc_record``）。

只读取 ``doc_publication.record_id`` 指向的生效快照作为回答来源；``snapshot_json`` 中的
``invisibleTeams`` / ``expireAt`` 决定拒绝范围与有效期。AI 仅核对内容归属与当前生效指针，
不核验审批过程，因此不映射审批人、工作流实例等字段。
"""

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.modules_gsi.base import SbpBase


class SbpKmDocRecordModel(SbpBase):
    """知识整篇内容快照。"""

    __tablename__ = "sbp_km_doc_record"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="记录主键")
    doc_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="知识主键")
    stage: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="记录阶段：save/owner/audience"
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="状态：saved/pending/approved/returned/cancelled",
    )
    snapshot_json: Mapped[str] = mapped_column(
        Text, nullable=False, comment="整篇知识不可变快照"
    )
