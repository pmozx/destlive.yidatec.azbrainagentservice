"""发布单元模型（表 ``sbp_ai_kb_publication``）：知识条目的统一主表。

本表同时承载：
1. **契约指针**：发布单元状态、版本指针、资源归属（03.1/03.3/03.4 写入）；
2. **管理端展示**：标题、分类、摘要、标签、维护人（管理页 CRUD）。

两条写入链路共用一行：契约首发时从 ``knowledge_no`` / ``metadata`` 派生标题默认值，
管理端后续补充分类、标签等归类信息。

历史：原 ``sbp_ai_kb_entry_meta`` 伴随表已于 v1.4（2026-11-03）合并入本表。
"""

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Index, String, Text, text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_model import ModelMixin


class KbPublicationModel(ModelMixin):
    """发布单元（表 sbp_ai_kb_publication）。"""

    __tablename__: str = "sbp_ai_kb_publication"
    __table_args__: tuple = (
        UniqueConstraint("publication_id", name="uq_sbp_ai_kb_publication_pid"),
        # 状态与类型的白名单落在 DDL 上：裸 SQL / 其它服务写入也绕不过去（D3）
        CheckConstraint("resource_type IN ('document', 'faq')", name="ck_sbp_ai_kb_publication_resource_type"),
        CheckConstraint("status IN ('active', 'inactive', 'deleted')", name="ck_sbp_ai_kb_publication_status"),
        CheckConstraint("current_revision >= 0", name="ck_sbp_ai_kb_publication_revision_non_negative"),
        Index("ix_sbp_ai_kb_publication_status_created", "status", "created_time"),
        # 归属校验的三元组：缺它就只能比 resource_type，拿到 publication_id 即可改写他人知识（P0-10）
        Index("ix_sbp_ai_kb_publication_tenant_resource", "tenant_id", "resource_type", "resource_id"),
        Index("ix_sbp_ai_kb_publication_category", "category"),
        {"comment": "发布单元（契约指针 + 管理端展示，统一主表）"},
    )

    # ── 契约指针字段 ─────────────────────────────────────────────────
    publication_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="GSI sbp_km_doc_publication.id")
    tenant_id: Mapped[str | None] = mapped_column(
        String(64),
        default=None,
        nullable=True,
        comment="契约 tenant_id（多租户归属；迁移期前写入的行可为 NULL，新写入必填）",
    )
    resource_id: Mapped[str | None] = mapped_column(
        String(64),
        default=None,
        nullable=True,
        comment="契约 resource_id（原始资源归属；迁移期前写入的行可为 NULL，新写入必填）",
    )
    resource_type: Mapped[str] = mapped_column(String(20), nullable=False, comment="document/faq")
    current_record_id: Mapped[str | None] = mapped_column(
        String(64), default=None, nullable=True, comment="当前生效记录（GSI 指针）"
    )
    current_revision: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"), nullable=False, comment="当前生效版本号（单调递增）")
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, nullable=True, comment="指针同步时间"
    )
    status: Mapped[str] = mapped_column(String(20), default="active", server_default=text("'active'"), nullable=False, comment="active/inactive/deleted")

    # ── 管理端展示/归类字段（合并自原 sbp_ai_kb_entry_meta）──────────────
    title: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        comment="条目标题（契约首发从 knowledge_no 派生，管理端可编辑）",
    )
    category: Mapped[str | None] = mapped_column(String(64), default=None, nullable=True, comment="知识分类")
    summary: Mapped[str | None] = mapped_column(Text, default=None, nullable=True, comment="内容摘要")
    tags: Mapped[list[str]] = mapped_column(
        JSONB,
        # 不写 server_default：库级默认值由 v1.4 迁移 SQL 建（``DEFAULT '[]'::jsonb``），
        # 模型里再写一遍会让 SQLite 建表报错（用例用内存库验证 CAS），纯属重复声明
        default=list,
        nullable=False,
        comment="标签（JSON 数组）",
    )
    owner: Mapped[str | None] = mapped_column(String(64), default=None, nullable=True, comment="维护人")
