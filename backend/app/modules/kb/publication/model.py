"""发布单元模型（表 ``sbp_ai_kb_publication``）：GSI 发布指针的镜像与生效状态，**不存内容**。"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_model import ModelMixin


class KbPublicationModel(ModelMixin):
    """发布单元（表 sbp_ai_kb_publication）。"""

    __tablename__: str = "sbp_ai_kb_publication"
    __table_args__: tuple = (
        UniqueConstraint("publication_id", name="uq_sbp_ai_kb_publication_pid"),
        Index("ix_sbp_ai_kb_publication_status_deleted", "status", "is_deleted"),
        Index("ix_sbp_ai_kb_publication_created_deleted", "created_time", "is_deleted"),
        {"comment": "发布单元（检索版本容器）"},
    )

    publication_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="GSI sbp_km_doc_publication.id")
    resource_type: Mapped[str] = mapped_column(String(20), nullable=False, comment="document/faq")
    current_record_id: Mapped[str | None] = mapped_column(
        String(64), default=None, nullable=True, comment="当前生效记录（GSI 指针）"
    )
    current_revision: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False, comment="当前生效版本号（单调递增）")
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, nullable=True, comment="指针同步时间"
    )
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False, comment="active/inactive/deleted")
