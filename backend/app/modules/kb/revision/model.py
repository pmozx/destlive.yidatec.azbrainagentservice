"""版本历史模型（表 ``sbp_ai_kb_revision``）：``(publication_id, revision)`` 唯一 + ``base_record_id`` 防并发覆盖。"""

from sqlalchemy import BigInteger, Boolean, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_model import ModelMixin


class KbRevisionModel(ModelMixin):
    """发布单元版本快照（表 sbp_ai_kb_revision）。"""

    __tablename__: str = "sbp_ai_kb_revision"
    __table_args__: tuple = (
        UniqueConstraint("publication_id", "revision", name="uq_sbp_ai_kb_revision_pub_rev"),
        Index("ix_sbp_ai_kb_revision_status_deleted", "status", "is_deleted"),
        Index("ix_sbp_ai_kb_revision_created_deleted", "created_time", "is_deleted"),
        Index("ix_sbp_ai_kb_revision_record", "record_id"),
        {"comment": "发布单元版本快照"},
    )

    publication_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="归属发布单元")
    record_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="本版本内容记录（sbp_km_doc_record.id）")
    base_record_id: Mapped[str | None] = mapped_column(String(64), default=None, nullable=True, comment="基线记录（防覆盖）")
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="版本号")
    content_hash: Mapped[str | None] = mapped_column(String(64), default=None, nullable=True, comment="内容指纹")
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="切片数")
    searchable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, comment="是否已可检索")
    status: Mapped[str] = mapped_column(String(20), default="online", nullable=False, comment="online/archived")
