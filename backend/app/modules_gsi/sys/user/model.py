"""GSI 业务表模型 · 模块 ``sys`` · 业务 ``user``（``public.sbp_sys_user`` 用户档案）。

映射范围是「管理台展示需要 + 不含敏感信息」的部分：账号、中英文名、职位、职级 ID
与三个有效性列。``password_hash`` / ``email`` / ``phone`` 一律不映射——未映射的列
不会被查询带出，即便 GSI 侧后续放宽数据库授权也不会顺着 ORM 泄漏。
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
    username: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="登录账号（K 账号，等于 employee.k_account）"
    )
    cn_name: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", comment="中文名"
    )
    en_name: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", comment="英文名"
    )
    rank_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, default=None, comment="职级ID → sbp_sys_rank.id"
    )
    position_name: Mapped[str] = mapped_column(
        String(128), nullable=False, default="", comment="职位名称（快照，不随组织变动刷新）"
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
