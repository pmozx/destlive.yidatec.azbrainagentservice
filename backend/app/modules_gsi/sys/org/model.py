"""GSI 业务表模型 · 模块 ``sys`` · 业务 ``org``（``public.sbp_sys_org`` 组织架构）。

只读映射契约允许的列；组织上下级仅提供上下文，不自动继承知识权限。
"""

from sqlalchemy import BigInteger, SmallInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from app.modules_gsi.base import SbpBase


class SbpSysOrgModel(SbpBase):
    """组织（部门/团队）。"""

    __tablename__ = "sbp_sys_org"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False, comment="主键"
    )
    parent_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="上级组织ID，0为顶级"
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="组织名称")
    code: Mapped[str] = mapped_column(String(64), nullable=False, comment="组织编码")
    type: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="节点类型：dept/team 等"
    )
    category: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="business",
        comment="组织分类：admin行政 business业务",
    )
    source_org_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        default=None,
        comment="业务镜像对应的行政组织ID；行政节点为空",
    )
    status: Mapped[str] = mapped_column(
        String(1), nullable=False, default="1", comment="状态：1启用 0停用"
    )
    deleted: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, comment="逻辑删除：0否 1是"
    )
