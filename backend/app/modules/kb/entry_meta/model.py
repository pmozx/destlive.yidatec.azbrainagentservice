"""知识条目元数据模型（表 ``sbp_ai_kb_entry_meta``）：管理端维护的展示与归类信息。

知识正文由契约链路写入 ``publication`` / ``revision`` / ``chunk``；本表只承载管理端可见的信息
（标题、分类、摘要、标签、维护人），一张发布单元一行，用 ``publication_id`` 与
``sbp_ai_kb_publication`` 一一对应。

单独建伴随表而不是给存量表加列：``create_missing_tables`` 只建缺失表，存量表的列变更需要
手写 DDL（规范 D17），伴随表可以让管理端能力随启动自动就绪，且与契约写入链路互不侵入。
"""

from typing import TYPE_CHECKING

from sqlalchemy import Index, String, text, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.base_model import ModelMixin

if TYPE_CHECKING:
    from ..publication.model import KbPublicationModel


class KbEntryMetaModel(ModelMixin):
    """知识条目元数据（表 sbp_ai_kb_entry_meta）。"""

    __tablename__: str = "sbp_ai_kb_entry_meta"
    __table_args__: tuple = (
        # 一张发布单元只允许一条管理端元数据，重复插入会直接撞唯一键（409 由全局 handler 归口）
        UniqueConstraint("publication_id", name="uq_sbp_ai_kb_entry_meta_pid"),
        Index("ix_sbp_ai_kb_entry_meta_category", "category"),
        {"comment": "知识条目元数据（管理端展示/归类，一张发布单元一行）"},
    )

    publication_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="归属发布单元（逻辑外键 → sbp_ai_kb_publication.publication_id）",
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False, comment="条目标题")
    category: Mapped[str | None] = mapped_column(String(64), default=None, nullable=True, comment="知识分类")
    summary: Mapped[str | None] = mapped_column(Text, default=None, nullable=True, comment="内容摘要")
    tags: Mapped[list[str]] = mapped_column(
        JSONB,
        default=list,
        server_default=text("'[]'::jsonb"),
        nullable=False,
        comment="标签（JSON 数组）",
    )
    owner: Mapped[str | None] = mapped_column(String(64), default=None, nullable=True, comment="维护人")

    # 逻辑外键没有 DDL 约束，关系映射用 primaryjoin 显式声明并设 viewonly（写入只走 publication_id）
    publication: Mapped["KbPublicationModel"] = relationship(
        "KbPublicationModel",
        primaryjoin="foreign(KbEntryMetaModel.publication_id) == KbPublicationModel.publication_id",
        viewonly=True,
    )
