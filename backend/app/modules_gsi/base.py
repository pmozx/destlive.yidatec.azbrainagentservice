"""GSI 业务库只读访问基类。

GSI 表由 GSI 侧 Flyway 创建与维护，AI 侧只读取、不建表、不写入：

- ``SbpBase`` 使用独立 ``metadata``，与 ``app.core.base_model.MappedBase`` 分离。
  ``app.bootstrap.ensure_all_tables()`` 只登记 AI 自有域（kb / system / voice），
  不会遍历到这里，因此项目启动时不会按模型初始化任何 GSI 表；
- 模型只映射契约允许读取的列（见《01-知识平台与AI接口对接说明-v3》第四、五节），
  不映射 ``password_hash`` 等无关敏感列，查询自然不会把它们带出来；
- ``SbpReadCRUD`` 只提供查询与分页方法，没有 create / update / delete。
"""

from typing import Any, TypeVar

from pydantic import BaseModel
from sqlalchemy import asc, desc, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncAttrs, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.sql.elements import ColumnElement

from app.core.base_schema import PageResultSchema
from app.core.exceptions import CustomException
from app.core.logger import logger

OutSchemaType = TypeVar("OutSchemaType", bound=BaseModel)


class SbpBase(AsyncAttrs, DeclarativeBase):
    """GSI 只读模型基类（独立 metadata，不参与启动建表）。"""

    __abstract__ = True


class SbpReadCRUD[ModelType: SbpBase]:
    """GSI 表只读数据访问：查询、分页、计数，不提供任何写操作。"""

    model: type[ModelType]

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get(self, **kwargs: Any) -> ModelType | None:
        """按字段等值查询第一条记录；未命中返回 ``None``。"""
        try:
            conditions = self._build_conditions(kwargs)
            sql = select(self.model).where(*conditions).limit(1)
            result = await self.db.execute(sql)
            return result.scalars().first()
        except SQLAlchemyError:
            logger.exception("GSI CRUD 查询失败：model={}", self.model.__name__)
            raise

    async def get_or_404(
        self,
        *,
        msg: str = "该数据不存在",
        out_schema: type[OutSchemaType] | None = None,
        **kwargs: Any,
    ) -> ModelType | OutSchemaType:
        """单条查询，未命中直接抛 404 语义的 ``CustomException``（规则 R4）。"""
        obj = await self.get(**kwargs)
        if obj is None:
            raise CustomException(msg=msg, status_code=404)
        return out_schema.model_validate(obj) if out_schema else obj

    async def get_list(
        self,
        *,
        conditions: list[ColumnElement] | None = None,
        order_by: list[dict[str, str]] | None = None,
        limit: int | None = None,
        **kwargs: Any,
    ) -> list[ModelType]:
        """按条件查询列表，默认按主键升序。"""
        try:
            where = self._build_conditions(kwargs)
            where.extend(conditions or [])
            sql = (
                select(self.model)
                .where(*where)
                .order_by(*self._parse_order(order_by or []))
            )
            if limit is not None:
                sql = sql.limit(limit)
            result = await self.db.execute(sql)
            return list(result.scalars().all())
        except SQLAlchemyError:
            logger.exception("GSI CRUD 列表查询失败：model={}", self.model.__name__)
            raise

    async def count(
        self,
        *,
        conditions: list[ColumnElement] | None = None,
        **kwargs: Any,
    ) -> int:
        """统计满足条件的记录数。"""
        try:
            where = self._build_conditions(kwargs)
            where.extend(conditions or [])
            sql = select(func.count()).select_from(self.model).where(*where)
            result = await self.db.execute(sql)
            return int(result.scalar() or 0)
        except SQLAlchemyError:
            logger.exception("GSI CRUD 计数失败：model={}", self.model.__name__)
            raise

    async def exists(self, **kwargs: Any) -> bool:
        """判断满足条件的记录是否存在。"""
        return await self.count(**kwargs) > 0

    async def page(
        self,
        offset: int,
        limit: int,
        order_by: list[dict[str, str]] | None = None,
        search: dict[str, Any] | None = None,
        out_schema: type[OutSchemaType] | None = None,
        extra_conditions: list[ColumnElement] | None = None,
    ) -> PageResultSchema[OutSchemaType] | PageResultSchema:
        """先 COUNT 再取当页数据，返回统一分页结构（分页/排序语义与基座 CRUD 一致）。"""
        try:
            conditions = self._build_conditions(search or {})
            conditions.extend(extra_conditions or [])
            count_sql = select(func.count()).select_from(self.model).where(*conditions)
            total = int((await self.db.execute(count_sql)).scalar() or 0)

            data_sql = (
                select(self.model)
                .where(*conditions)
                .order_by(*self._parse_order(order_by or []))
                .offset(offset)
                .limit(limit)
            )
            result = await self.db.execute(data_sql)
            objs = result.scalars().all()
            items = (
                [out_schema.model_validate(obj) for obj in objs]
                if out_schema
                else list(objs)
            )

            return PageResultSchema(
                page_no=offset // limit + 1 if limit else 1,
                page_size=limit or 10,
                total=total,
                has_next=offset + limit < total,
                items=items,
            )
        except SQLAlchemyError:
            logger.exception("GSI CRUD 分页失败：model={}", self.model.__name__)
            raise

    def _build_conditions(self, values: dict[str, Any]) -> list[ColumnElement]:
        """字段名 → 条件；值为 ``(操作符, 值)`` 时按 like/in/between 等解析。

        未知字段与空串按「不筛选」跳过（与基座 ``CRUDBase._build_conditions`` 一致），
        避免前端传错字段导致 500。
        """
        conditions: list[ColumnElement] = []
        for field, value in values.items():
            if value is None or value == "":
                continue
            column = getattr(self.model, field, None)
            if column is None:
                logger.debug(
                    "查询字段 {} 不在模型 {} 上，已忽略", field, self.model.__name__
                )
                continue
            if isinstance(value, tuple):
                conditions.extend(self._resolve_condition(column, value))
            elif isinstance(value, (list, set)):
                conditions.append(column.in_(value))
            else:
                conditions.append(column == value)
        return conditions

    @staticmethod
    def _resolve_condition(column: ColumnElement, value: tuple) -> list[ColumnElement]:
        """``(op, val)`` → SQLAlchemy 条件，支持 eq/like/in/between/None/not None。"""
        op, val = value
        if op == "None":
            return [column.is_(None)]
        if op == "not None":
            return [column.isnot(None)]
        if val is None:
            return []
        if op == "like":
            return [column.like(f"%{val}%")]
        if op == "in":
            return [column.in_(val)]
        if op == "between" and isinstance(val, (list, tuple)) and len(val) == 2:
            return [column.between(val[0], val[1])]
        return [column == val]

    def _parse_order(self, order: list[dict[str, str]]) -> list[ColumnElement]:
        """``[{"field": "asc|desc"}]`` → 排序子句；非法字段跳过，缺省按全部主键升序。"""
        columns: list[ColumnElement] = []
        for item in order:
            for field, direction in item.items():
                if field not in self.model.__table__.columns:
                    continue
                column = getattr(self.model, field)
                columns.append(
                    desc(column) if str(direction).lower() == "desc" else asc(column)
                )
        return columns or [
            column.asc() for column in self.model.__table__.primary_key.columns
        ]
