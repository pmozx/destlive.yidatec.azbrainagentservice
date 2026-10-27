from datetime import datetime, UTC

from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, declared_attr
from sqlalchemy import DateTime, Integer, Index, String, Boolean, inspect

from app.utils.common_util import uuid4_str


class MappedBase(AsyncAttrs, DeclarativeBase):
    """声明式基类

    `AsyncAttrs <https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html#sqlalchemy.ext.asyncio.AsyncAttrs>`__

    `DeclarativeBase <https://docs.sqlalchemy.org/en/20/orm/declarative_config.html>`__

    `mapped_column() <https://docs.sqlalchemy.org/en/20/orm/mapping_api.html#sqlalchemy.orm.mapped_column>`__

    兼容 SQLite、MySQL 和 PostgreSQL
    """

    __abstract__: bool = True

    @declared_attr.directive
    def __tablename__(cls) -> str:
        return cls.__name__.lower()

    def __repr__(self):
        """统一输出所有字段"""
        columns = {c.key: getattr(self, c.key) for c in inspect(self).mapper.column_attrs}
        # 格式化输出，对字符串加引号
        parts = []
        for key, value in columns.items():
            if isinstance(value, str):
                parts.append(f"{key}='{value}'")
            else:
                parts.append(f"{key}={value}")
        return f"<{self.__class__.__name__}({', '.join(parts)})>"

    def to_dict(self):
        """转为字典"""
        return {c.key: getattr(self, c.key) for c in inspect(self).mapper.column_attrs}

class ModelMixin(MappedBase):
    """模型混入类 - 提供通用字段和功能

    基础模型混合类 Mixin: 一种面向对象编程概念, 使结构变得更加清晰

    数据隔离设计原则：
    ==================
    数据权限 (created_id/updated_id):
        - 配合角色的data_scope字段实现精细化权限控制
        - 1:仅本人
        - 2:本部门
        - 3:本部门及以下
        - 4:全部数据
        - 5:自定义

    SQLAlchemy加载策略说明:
    - select(默认): 延迟加载,访问时单独查询
    - joined: 使用LEFT JOIN预加载
    - selectin: 使用IN查询批量预加载(推荐用于一对多)
    - subquery: 使用子查询预加载
    - raise/raise_on_sql: 禁止加载
    - noload: 不加载,返回None
    - immediate: 立即加载
    - write_only: 只写不读
    - dynamic: 返回查询对象,支持进一步过滤
    """

    __abstract__: bool = True

    @declared_attr.directive
    def __table_args__(cls) -> tuple:
        table_name = cls.__tablename__ if hasattr(cls, '__tablename__') else cls.__name__.lower()
        return (
            Index(f"ix_{table_name}_status_deleted", "status", "is_deleted"),
            Index(f"ix_{table_name}_created_deleted", "created_time", "is_deleted"),
        )

    # 基础字段
    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
        comment="主键ID",
        index=True,
    )
    uuid: Mapped[str] = mapped_column(
        String(64),
        default=uuid4_str,
        nullable=False,
        unique=True,
        comment="UUID全局唯一标识",
        index=True,
    )
    is_deleted: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        comment="是否已删除(0:未删除 1:已删除)",
        index=True,
    )
    created_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
        comment="创建时间",
        index=True,
    )
    updated_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
        comment="更新时间",
    )
    deleted_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        default=None,
        nullable=True,
        comment="删除时间",
    )