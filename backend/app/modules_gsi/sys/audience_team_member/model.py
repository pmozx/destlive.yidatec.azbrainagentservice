"""GSI 业务表模型 · 模块 ``sys`` · 业务 ``audience_team_member``（``public.sbp_sys_audience_team_member``）。

按签名 ``user_id`` 查询，不使用姓名；仅提供团队归属，不代表权限本身。
"""

from sqlalchemy import BigInteger
from sqlalchemy.orm import Mapped, mapped_column

from app.modules_gsi.base import SbpBase


class SbpSysAudienceTeamMemberModel(SbpBase):
    """受众团队成员关联（复合主键 team_id + user_id）。"""

    __tablename__ = "sbp_sys_audience_team_member"

    team_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False, comment="受众团队ID"
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False, comment="用户ID"
    )
