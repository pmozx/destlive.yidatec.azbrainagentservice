"""GSI 业务表模型 · 模块 ``sys`` · 业务 ``org_member``（``public.sbp_sys_org_member`` 组织成员）。

仅提供组织归属，不代替受众团队成员关系。
"""

from sqlalchemy import BigInteger
from sqlalchemy.orm import Mapped, mapped_column

from app.modules_gsi.base import SbpBase


class SbpSysOrgMemberModel(SbpBase):
    """组织成员关联（复合主键 org_id + user_id）。"""

    __tablename__ = "sbp_sys_org_member"

    org_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False, comment="组织ID"
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False, comment="用户ID"
    )
