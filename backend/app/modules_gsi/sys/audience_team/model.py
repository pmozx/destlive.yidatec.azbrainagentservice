"""GSI 业务表模型 · 模块 ``sys`` · 业务 ``audience_team``（``public.sbp_sys_audience_team`` 受众团队）。

仅 ``status='1'`` 且 ``deleted=0`` 有效；``group_code`` 为唯一编码，``group_name`` /
``audience_team`` 仅用于兼容历史标识匹配。
"""

from sqlalchemy import BigInteger, SmallInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from app.modules_gsi.base import SbpBase


class SbpSysAudienceTeamModel(SbpBase):
    """受众团队（知识权限组）。"""

    __tablename__ = "sbp_sys_audience_team"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False, comment="主键"
    )
    group_code: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="团队编码"
    )
    group_name: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="团队名称"
    )
    audience_team: Mapped[str | None] = mapped_column(
        String(128), nullable=True, default=None, comment="受众团队标识"
    )
    leader_name: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", comment="负责人姓名（字符串快照，不是 user_id）"
    )
    status: Mapped[str] = mapped_column(
        String(1), nullable=False, default="1", comment="状态：1启用 0停用"
    )
    remark: Mapped[str] = mapped_column(
        String(512), nullable=False, default="", comment="备注"
    )
    deleted: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, comment="逻辑删除：0否 1是"
    )
