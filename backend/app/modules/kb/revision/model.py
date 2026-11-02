"""版本历史模型（表 ``sbp_ai_kb_revision``）：``(publication_id, revision)`` 唯一 + ``base_record_id`` 防并发覆盖。"""

from sqlalchemy import BigInteger, Boolean, Index, Integer, String, text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_model import ModelMixin


class KbRevisionModel(ModelMixin):
    """发布单元版本快照（表 sbp_ai_kb_revision）。"""

    __tablename__: str = "sbp_ai_kb_revision"
    __table_args__: tuple = (
        UniqueConstraint("publication_id", "revision", name="uq_sbp_ai_kb_revision_pub_rev"),
        Index("ix_sbp_ai_kb_revision_status_created", "status", "created_time"),
        Index("ix_sbp_ai_kb_revision_record", "record_id"),
        # #04 精读按契约 version_id 回查：它不是主键也不是 revision 的别名，必须独立索引
        Index("ix_sbp_ai_kb_revision_version_id", "version_id"),
        {"comment": "发布单元版本快照"},
    )

    publication_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="归属发布单元")
    record_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="本版本内容记录（sbp_km_doc_record.id）")
    version_id: Mapped[str | None] = mapped_column(
        String(64),
        default=None,
        nullable=True,
        comment="契约 version_id（#04 按版本精读；存量行 NULL）",
    )
    knowledge_no: Mapped[str | None] = mapped_column(
        String(64),
        default=None,
        nullable=True,
        comment="契约 knowledge_no（对外可读编号；存量行 NULL）",
    )
    # 列名就是契约字段名 metadata；属性必须改名，否则与 Declarative 的 .metadata（MetaData 对象）冲突
    metadata_: Mapped[dict | None] = mapped_column(
        "metadata",
        JSONB,
        default=None,
        nullable=True,
        comment="契约透传元数据（原样保存，不参与业务判定）",
    )
    base_record_id: Mapped[str | None] = mapped_column(String(64), default=None, nullable=True, comment="基线记录（防覆盖）")
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="版本号")
    content_hash: Mapped[str | None] = mapped_column(String(64), default=None, nullable=True, comment="内容指纹")
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"), nullable=False, comment="切片数")
    searchable: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"), nullable=False, comment="是否已可检索")
    status: Mapped[str] = mapped_column(String(20), default="online", server_default=text("'online'"), nullable=False, comment="online/archived")
