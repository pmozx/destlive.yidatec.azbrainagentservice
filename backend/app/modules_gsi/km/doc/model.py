"""GSI 业务表模型 · 模块 ``km`` · 业务 ``doc``（``public.sbp_km_doc`` 知识文档主表）。

契约手册中本表字段行在原始 HTML 中已损坏，无法恢复。这里只映射权限判断与展示所需的
元数据列（``id/title/status/doc_type/domain/team/expire_at/knowledge_no/deleted``），
不映射正文、AI 解析态与审批扩展字段；回答正文以 ``sbp_km_doc_record.snapshot_json`` 为准。
"""

from datetime import date

from sqlalchemy import Date, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.modules_gsi.base import SbpBase


class SbpKmDocModel(SbpBase):
    """知识文档（元数据只读视图）。"""

    __tablename__ = "sbp_km_doc"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="主键")
    title: Mapped[str] = mapped_column(String(512), nullable=False, comment="知识标题")
    status: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="Draft",
        comment="业务状态：Draft/Published/Unpublished 等",
    )
    doc_type: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="Document",
        comment="知识类型：Document/FAQ/SOP/API/Handover 等",
    )
    domain: Mapped[str | None] = mapped_column(
        String(128), nullable=True, default=None, comment="业务域/分类"
    )
    team: Mapped[str | None] = mapped_column(
        String(128), nullable=True, default=None, comment="归属业务团队"
    )
    expire_at: Mapped[date | None] = mapped_column(
        Date, nullable=True, default=None, comment="有效期截止日期"
    )
    knowledge_no: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
        default=None,
        comment="知识编号：KB-0000001 / FAQ-0000001",
    )
    deleted: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="逻辑删除：0否 1是"
    )
