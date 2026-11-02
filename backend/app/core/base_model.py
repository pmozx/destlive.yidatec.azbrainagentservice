from datetime import datetime, UTC

from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, declared_attr
from sqlalchemy import DateTime, Integer, String, inspect, text

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

    删除语义（v1.7 起）：
    ==================
    基座**不提供软删除**。本 Mixin 只有 ``id`` / ``uuid`` / ``created_time`` / ``updated_time``
    四类通用字段，没有 ``is_deleted`` / ``deleted_time`` / ``deleted_id``；
    ``CRUDBase.delete()`` 一律物理删除，``restore()`` 已随之移除。

    需要"保留痕迹"时由领域自己表达，而不是由基座猜测：
    - ``sbp_ai_kb_revision``：删除留墓碑行（``status='archived'`` + ``searchable=false``），
      用于拒绝旧 revision 的重复提交，且本身就该作为删除事件被查到；
    - ``sbp_ai_kb_chunk``：删除必须**物理删**切片，软删的向量仍会被检索出来（#06 要求删后不可召回）。

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
        """基座不再为所有表统一加 ``(status, is_deleted)`` / ``(created_time, is_deleted)`` 复合索引。

        软删除已整体移除（v1.7），原先那两个复合索引会退化成单列索引；``created_time``
        自带 ``index=True`` 已覆盖时间排序，业务若需要「按状态筛 + 按时间排」请在自己模型的
        ``__table_args__`` 里显式声明 ``(status, created_time)``。

        保留这个空实现而不是删掉：子类一旦定义 ``__table_args__`` 仍应整体覆盖，语义不变。
        """
        return ()

    # 基础字段
    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
        comment="主键ID",
        # 不要加 index=True：主键本身已有唯一索引（<table>_pkey），再加会多出一个
        # 完全重复的 ``ix_<table>_id``，只带来写放大，优化器永远不会选中它。
        # 这一个参数曾让 16 张表各多一条冗余索引（报告 A-1 / 规范 D12）。
    )
    uuid: Mapped[str] = mapped_column(
        String(64),
        default=uuid4_str,
        nullable=False,
        unique=True,
        comment="UUID全局唯一标识",
        index=True,
    )
    created_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=text("now()"),
        nullable=False,
        comment="创建时间",
        index=True,
    )
    updated_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        server_default=text("now()"),
        nullable=False,
        comment="更新时间",
    )