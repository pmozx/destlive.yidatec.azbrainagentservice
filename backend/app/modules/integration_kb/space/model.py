"""集成-知识库-知识空间模型（表 sbp_ai_integration_kb_space）。"""

from sqlalchemy import Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_model import ModelMixin

INTEGRATION_KB_SPACE_TABLE_NAME: str = "sbp_ai_integration_kb_space"


class IntegrationKbSpaceModel(ModelMixin):
    """知识空间表

    ``code`` 是空间的稳定标识：对象存储目录按它划分，分片表冗余它便于按空间召回与批量清理，
    因此创建后不可修改（改 code 需级联改文档与分片）。
    """

    __tablename__: str = INTEGRATION_KB_SPACE_TABLE_NAME
    __table_args__: tuple = (
        Index("uq_sbp_ai_integration_kb_space_code", "code", unique=True),
        Index("ix_sbp_ai_integration_kb_space_sort", "sort"),
        {"comment": "集成-知识库-知识空间表"},
    )

    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="空间名称")
    code: Mapped[str] = mapped_column(String(64), nullable=False, comment="空间编码（对象存储目录/分片空间标识）")
    icon: Mapped[str] = mapped_column(String(64), default="", nullable=False, comment="图标标识")
    color: Mapped[str] = mapped_column(String(32), default="", nullable=False, comment="主题色")
    description: Mapped[str] = mapped_column(Text, default="", nullable=False, comment="空间说明")
    security_level: Mapped[int] = mapped_column(Integer, default=1, nullable=False, comment="密级(1:公开 2:内部 3:机密)")
    sort: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="排序(升序)")
    doc_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="文档数(冗余计数)")
    status: Mapped[int] = mapped_column(Integer, default=0, nullable=False, index=True, comment="状态(0:启用 1:停用)")
