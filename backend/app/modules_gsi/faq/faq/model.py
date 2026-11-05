"""GSI 业务表模型 · 模块 ``faq`` · 业务 ``faq``（``public.sbp_faq`` FAQ 主记录）。

只映射契约 §四 指定的列（``id`` / ``faq_no`` / ``status`` / ``deleted``）：
``question`` / ``answer`` 正文不在授权范围内，未映射的列不会被查询带出。
"""

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.modules_gsi.base import SbpBase


class SbpFaqModel(SbpBase):
    """FAQ 主记录（限字段只读视图）。"""

    __tablename__ = "sbp_faq"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="主键")
    faq_no: Mapped[str | None] = mapped_column(
        String(32), nullable=True, default=None, comment="FAQ 编号：FAQ-0000001"
    )
    status: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="Draft",
        comment="业务状态：Draft/Pending Owner Review/Published/Unpublished 等",
    )
    deleted: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="逻辑删除：0否 1是"
    )
