"""GSI 业务表模型 · 模块 ``sys`` · 业务 ``user``（``public.sbp_sys_user`` 用户有效性）。

只映射 ``id / status / deleted / allow_login``：契约明确不授予 ``password_hash``、邮箱、
手机号等无关列，未映射的列不会被查询带出。
"""

from sqlalchemy import BigInteger, SmallInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from app.modules_gsi.base import SbpBase


class SbpSysUserModel(SbpBase):
    """系统用户（限字段只读视图）。"""

    __tablename__ = "sbp_sys_user"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False, comment="主键"
    )
    status: Mapped[str] = mapped_column(
        String(1), nullable=False, default="1", comment="在职状态：1在职 0离职"
    )
    allow_login: Mapped[str] = mapped_column(
        String(1), nullable=False, default="1", comment="是否允许登录：1允许 0禁止"
    )
    deleted: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, comment="逻辑删除：0否 1是"
    )
