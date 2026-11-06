"""GSI 业务表模型 · 模块 ``sys`` · 业务 ``rank``（``public.sbp_sys_rank`` 职级主数据）。

纯引用表：为 ``sbp_sys_user.rank_id`` 提供可读的职级名称，不参与任何授权判定，
也不含敏感列。
"""

from sqlalchemy import BigInteger, Integer, SmallInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from app.modules_gsi.base import SbpBase


class SbpSysRankModel(SbpBase):
    """职级（用户档案的展示维度）。"""

    __tablename__ = "sbp_sys_rank"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False, comment="主键"
    )
    rank_code: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="职级编码"
    )
    rank_name: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="职级名称"
    )
    sort_no: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="排序号"
    )
    status: Mapped[str] = mapped_column(
        String(1), nullable=False, default="1", comment="状态：1启用 0停用"
    )
    deleted: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, comment="逻辑删除：0否 1是"
    )
