"""重复分组模型（表 ``sbp_ai_kb_dup_group``）：``uuid`` 即契约 ``group_id``，合并预览只暂存本表。"""

from decimal import Decimal
from typing import Any

from sqlalchemy import Index, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_model import ModelMixin


class KbDupGroupModel(ModelMixin):
    """重复分组（表 sbp_ai_kb_dup_group，#16–#20 治理物化）。"""

    __tablename__: str = "sbp_ai_kb_dup_group"
    __table_args__: tuple = (
        Index("ix_sbp_ai_kb_dup_group_status_deleted", "status", "is_deleted"),
        Index("ix_sbp_ai_kb_dup_group_created_deleted", "created_time", "is_deleted"),
        Index("ix_sbp_ai_kb_dup_group_status_created", "status", "created_time"),
        {"comment": "重复分组（#16–#20 治理物化）"},
    )

    resource_type: Mapped[str] = mapped_column(String(20), nullable=False, comment="资源类型")
    scope: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None, nullable=True, comment="检测范围")
    members: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False, comment="组内条目")
    similarity: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), default=None, nullable=True, comment="组内最高相似度")
    merged_preview: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, default=None, nullable=True, comment="合并预览（草稿态，不落正式库）"
    )
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False, comment="active/merged/dismissed")
