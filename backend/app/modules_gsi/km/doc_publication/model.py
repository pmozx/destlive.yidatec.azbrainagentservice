"""GSI 业务表模型 · 模块 ``km`` · 业务 ``doc_publication``（``public.sbp_km_doc_publication``）。

文档按团队生效关系：``team`` 与用户受众团队标识匹配，``record_id`` 指向当前生效快照；
``index_status`` 只是索引同步状态，不能单独作为授权依据。
"""

from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.modules_gsi.base import SbpBase


class SbpKmDocPublicationModel(SbpBase):
    """知识按受众团队生效内容。"""

    __tablename__ = "sbp_km_doc_publication"

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, comment="生效记录主键"
    )
    doc_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="知识主键")
    team: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="获授权受众团队"
    )
    record_id: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="当前生效的整篇送审快照"
    )
    index_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
        comment="索引同步状态：pending/succeeded/failed",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, comment="生效更新时间"
    )
