"""GSI 业务表模型 · 模块 ``km`` · 业务 ``doc_record``（``public.sbp_km_doc_record``）。

只读取 ``doc_publication.record_id`` 指向的生效快照作为回答来源；``snapshot_json`` 中的
``invisibleTeams`` / ``expireAt`` 决定拒绝范围与有效期。AI 仅核对内容归属与当前生效指针，
不核验审批过程：``created_by`` / ``team`` / ``assignee`` / ``created_at`` / ``comment``
只为页面展示「谁在什么时候送审给哪个团队、审批意见是什么」而映射，
判定逻辑一行都不读它们。
"""

from datetime import datetime

from sqlalchemy import DateTime, String, Text
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
    team: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="受众团队编码，内容审为空"
    )
    assignee: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="审批人账号"
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="状态：saved/pending/approved/returned/cancelled",
    )
    snapshot_json: Mapped[str] = mapped_column(
        Text, nullable=False, comment="整篇知识不可变快照"
    )
    # ``comment`` 是 SQL 关键字，属性名与列名同名时显式把列名交出去，避免被拼进 DDL/SQL 时出错
    comment: Mapped[str | None] = mapped_column(
        "comment", Text, nullable=True, comment="保存说明或审批意见"
    )
    created_by: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="送审人账号"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, comment="送审时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, comment="更新时间"
    )
