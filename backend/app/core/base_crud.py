from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timedelta
from functools import wraps
from typing import Any, TypeVar, cast

from fastapi import status
from pydantic import BaseModel
from sqlalchemy import asc, delete, desc, false, func, select, update
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.engine import Result
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, load_only, selectinload
from sqlalchemy.sql.elements import ColumnElement

from app.core.base_model import ModelMixin
from app.core.base_schema import PageResultSchema
from app.core.exceptions import CustomException
from app.core.logger import logger

OutSchemaType = TypeVar("OutSchemaType", bound=BaseModel)
CreateSchemaType = TypeVar("CreateSchemaType", bound=BaseModel)
UpdateSchemaType = TypeVar("UpdateSchemaType", bound=BaseModel)

# 操作符 → 方法名映射，留给 _resolve_condition 运行时根据具体 attr 调用
# 因为不同列的 ColumnElement 类型不同，不能提取为类级常量
_OPERATOR_MAP: dict[str, str] = {
    "!=": "__ne__", "ne": "__ne__",
    ">": "__gt__", "gt": "__gt__",
    ">=": "__ge__", "ge": "__ge__",
    "<": "__lt__", "lt": "__lt__",
    "<=": "__le__", "le": "__le__",
    "eq": "__eq__", "==": "__eq__",
}


def _sql_errors(method: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    """DB 异常统一出口：记日志后原样上抛，**不**包 CustomException。

    包起来会把唯一约束冲突压成同一个 500；全局 handler 要按异常类型映射
    （IntegrityError → 409 等）。方法名取自函数本身，避免手写字面量错配。
    """

    @wraps(method)
    async def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return await method(self, *args, **kwargs)
        except SQLAlchemyError:
            logger.exception("CRUD {} 失败：model={}", method.__name__, self.model.__name__)
            raise

    return wrapper


class CRUDBase[ModelType: ModelMixin, CreateSchemaType, UpdateSchemaType]:
    """事务边界在 HTTP 层（db_getter 有 session.begin()），CRUD 只 flush 不 commit。

    CRUD 层只自动填充 created_id/updated_id，不按这些字段过滤数据。
    数据权限由 Service 层负责 —— Service 层忘记过滤 = 越权风险。
    """

    def __init__(self, model: type[ModelType], db: AsyncSession) -> None:
        self.model = model
        self.db = db

    # ── 辅助方法 ──────────────────────────────────────────────────────

    def _get_pk_col(self) -> ColumnElement:
        """获取模型的主键列。delete/set/restore/page 批量操作共用。"""
        mapper = sa_inspect(self.model)
        pk_cols = list(mapper.primary_key)
        if not pk_cols:
            raise CustomException(msg="模型缺少主键")
        if len(pk_cols) > 1:
            raise CustomException(msg="暂不支持复合主键操作")
        return pk_cols[0]

    async def _with_audit_relations(self, obj: ModelType) -> ModelType:
        """回读 created_by / updated_by：列表页都要展示审计人，写完统一补齐避免 N+1。"""
        relationships = getattr(sa_inspect(self.model), "relationships", {})
        options = [
            joinedload(getattr(self.model, name)) for name in ("created_by", "updated_by") if name in relationships
        ]
        if not options:
            return obj
        result = await self.db.execute(select(self.model).options(*options).where(self._get_pk_col() == obj.id))
        return result.scalar_one()

    # ── 查询 ──────────────────────────────────────────────────────────

    @_sql_errors
    async def get(self, preload: list[str | Any] | None = None, **kwargs) -> ModelType | None:
        """单条查询：按字段等值匹配返回第一条记录，未找到返回 None。"""
        conditions = await self._build_conditions(**kwargs)
        sql = select(self.model).where(*conditions)
        for opt in self._loader_options(preload):
            sql = sql.options(opt)
        result: Result = await self.db.execute(sql)
        return result.scalars().first()

    async def get_or_404(
        self,
        id: int | None = None,
        msg: str = "该数据不存在",
        preload: list[str | Any] | None = None,
        out_schema: type[OutSchemaType] | None = None,
        **kwargs,
    ) -> ModelType | OutSchemaType:
        """单条查询，未找到时抛 404；给了 ``out_schema`` 就顺手转成出参模型。

        ``id`` 只是 ``kwargs["id"]`` 的快捷入参，可与其它条件叠加。
        """
        if id is not None:
            kwargs["id"] = id
        obj = await self.get(preload=preload, **kwargs)
        if not obj:
            # docstring 承诺 404，而 CustomException 旧默认值是 500；这里显式钉住语义
            raise CustomException(msg=msg, status_code=status.HTTP_404_NOT_FOUND)
        return out_schema.model_validate(obj) if out_schema else obj

    @_sql_errors
    async def exists(self, **kwargs) -> bool:
        """判断满足条件的记录是否存在（内部走 COUNT，不加载行数据）。"""
        return await self.count(**kwargs) > 0

    @_sql_errors
    async def count(
        self,
        extra_conditions: Sequence[ColumnElement] | None = None,
        **kwargs,
    ) -> int:
        """统计满足条件的记录行数（SELECT COUNT）。"""
        conditions = await self._build_conditions(**kwargs)
        conditions.extend(extra_conditions or [])
        count_sql = select(func.count()).select_from(self.model).where(*conditions)
        result: Result = await self.db.execute(count_sql)
        return result.scalar() or 0

    @_sql_errors
    async def get_list(
        self,
        search: dict[str, Any] | None = None,
        order_by: list[dict[str, str]] | None = None,
        preload: list[str | Any] | None = None,
        load_columns: list | None = None,
        limit: int | None = None,
        extra_conditions: Sequence[ColumnElement] | None = None,
    ) -> Sequence[ModelType]:
        """不分页列表查询；``search`` 格式同 :meth:`page`，``load_columns`` 只取需要的列。"""
        conditions = await self._build_conditions(**(search or {}))
        conditions.extend(extra_conditions or [])
        order = order_by or [{"id": "asc"}]
        sql = select(self.model).where(*conditions).order_by(*self._parse_order(order))
        if limit is not None:
            sql = sql.limit(limit)
        if load_columns:
            sql = sql.options(load_only(*load_columns))
        for opt in self._loader_options(preload):
            sql = sql.options(opt)
        result: Result = await self.db.execute(sql)
        return result.scalars().all()

    @_sql_errors
    async def page(
        self,
        offset: int,
        limit: int,
        order_by: list[dict[str, str]],
        search: dict[str, Any] | None = None,
        out_schema: type[OutSchemaType] | None = None,
        preload: list[str | Any] | None = None,
        load_columns: list | None = None,
        extra_conditions: Sequence[ColumnElement] | None = None,
    ) -> PageResultSchema[OutSchemaType] | PageResultSchema:
        """分页查询：先 COUNT 总数再取当页，返回统一分页结构。

        ``offset``/``limit`` 由 Service 层用 ``page_no``/``page_size`` 换算。COUNT 从
        ``data_sql`` 的 WHERE 派生并去掉 loader option，避免为统计再付一次 LEFT JOIN。
        """
        conditions = await self._build_conditions(**(search or {}))
        conditions.extend(extra_conditions or [])
        order = order_by or [{"id": "asc"}]

        pk = self._get_pk_col()  # COUNT 用主键列更精确

        data_sql = select(self.model).where(*conditions)
        if load_columns:
            data_sql = data_sql.options(load_only(*load_columns))
        for opt in self._loader_options(preload):
            data_sql = data_sql.options(opt)

        count_sql = select(func.count(pk)).select_from(self.model)
        where_clause = data_sql.whereclause
        if where_clause is not None:
            count_sql = count_sql.where(where_clause)

        total_result = await self.db.execute(count_sql)
        total = total_result.scalar() or 0

        result: Result = await self.db.execute(data_sql.order_by(*self._parse_order(order)).offset(offset).limit(limit))
        objs = result.scalars().all()

        items = [out_schema.model_validate(obj) for obj in objs] if out_schema else list(objs)

        return PageResultSchema(
            page_no=offset // limit + 1 if limit else 1,
            page_size=limit or 10,
            total=total,
            has_next=offset + limit < total,
            items=items,
        )

    # ── 写入 ──────────────────────────────────────────────────────────

    @_sql_errors
    async def create(self, data: CreateSchemaType | dict[str, Any]) -> ModelType:
        """新增一条记录并返回（Schema 走 ``exclude_none``，已 flush + refresh）。"""
        obj_dict = data.model_dump(exclude_none=True) if isinstance(data, BaseModel) else cast("dict[str, Any]", data)
        obj = self.model(**obj_dict)

        self.db.add(obj)
        await self.db.flush()
        await self.db.refresh(obj)
        return await self._with_audit_relations(obj)

    @_sql_errors
    async def update(self, id: int, data: UpdateSchemaType | dict[str, Any]) -> ModelType:
        """按主键更新（Schema 走 ``exclude_unset + exclude_none``，只动调用方传入的字段）。"""
        obj_dict = (
            data.model_dump(exclude_unset=True, exclude_none=True, exclude={"id"})
            if isinstance(data, BaseModel)
            else cast("dict[str, Any]", data)
        )
        obj = await self.get(id=id)
        if not obj:
            raise CustomException(msg="更新对象不存在", status_code=status.HTTP_400_BAD_REQUEST)

        for key, value in obj_dict.items():
            if hasattr(obj, key):
                setattr(obj, key, value)

        await self.db.flush()
        await self.db.refresh(obj)
        return await self._with_audit_relations(obj)

    @_sql_errors
    async def delete(self, ids: list[int]) -> None:
        """按主键批量物理删除——不可恢复；需要留痕由业务自己写墓碑（见 ``ModelMixin``）。"""
        pk = self._get_pk_col()
        await self.db.execute(delete(self.model).where(pk.in_(ids)))
        await self.db.flush()

    @_sql_errors
    async def delete_by_conditions(self, *conditions: ColumnElement) -> int:
        """按条件批量物理删除，返回删除行数（日志/台账过期清理用）。"""
        if not conditions:
            raise CustomException(msg="条件删除失败，删除条件不能为空")
        result = await self.db.execute(delete(self.model).where(*conditions))
        await self.db.flush()
        return int(getattr(result, "rowcount", 0) or 0)

    @_sql_errors
    async def set(self, ids: list[int], **kwargs) -> None:
        """按主键批量更新指定字段（UPDATE ... SET field=value）。"""
        pk = self._get_pk_col()
        sql = update(self.model).where(pk.in_(ids)).values(**kwargs)
        await self.db.execute(sql)
        await self.db.flush()

    # ── 条件与排序 ────────────────────────────────────────────────────

    async def _build_conditions(self, **kwargs) -> list[ColumnElement]:
        """根据 kwargs 动态拼接 WHERE 条件列表。

        值类型决定比较方式：
        - tuple     → 委托 _resolve_condition（like/in/between/date/null/比较操作符）
        - 其他      → 等值比较兜底（仅兼容 ``get(id=1)`` 等直接关键字传参）

        None / 空串的键值对跳过，不做条件。

        提示：分页/列表/统计查询走 ``search_to_dict`` 后 kwargs 值均为 tuple，
        由 Schema 层的 ``json_schema_extra={"q": "..."}`` 精确控制操作符。
        """
        conditions: list[ColumnElement] = []

        for key, value in kwargs.items():
            if value is None or value == "":
                continue
            if not hasattr(self.model, key):
                # ``BaseQueryParam`` 无条件继承了 ``updated_time``，而台账类表（nonce /
                # idempotency / request_log / auth_cache / parse_result / eval_case）没有该列：
                # 前端一传时间范围这里就 AttributeError → 500。字段不存在即跳过，按「不筛选」处理。
                logger.debug("查询字段 {} 不在模型 {} 上，已忽略", key, self.model.__name__)
                continue
            attr = getattr(self.model, key)
            if isinstance(value, tuple):
                conditions.extend(self._resolve_condition(attr, value))
            else:
                conditions.append(attr == value)
        return conditions

    @staticmethod
    def _resolve_condition(attr: ColumnElement, value: tuple) -> list[ColumnElement]:
        """元组条件 `(seq, val)` → SQLAlchemy condition。

        seq 支持：None / not None / date / month / like / in / between / 比较操作符。

        date 返回 [>=当天0:00, <第二天0:00) 的范围，
        month 返回 [>=1号0:00, <下月1号0:00) 的范围。
        """
        seq, val = value

        # 先处理不依赖 val 的 IS NULL / IS NOT NULL
        handlers: dict[str, Any] = {
            "None": lambda: [attr.is_(None)],
            "not None": lambda: [attr.isnot(None)],
        }
        if seq in handlers:
            return handlers[seq]()

        if val is None:
            return []

        if seq == "date":
            dt = datetime.strptime(val, "%Y-%m-%d")
            return [attr >= dt, attr < dt + timedelta(days=1)]
        if seq == "month":
            dt = datetime.strptime(val, "%Y-%m")
            next_month = dt.replace(year=dt.year + 1, month=1) if dt.month == 12 else dt.replace(month=dt.month + 1)
            return [attr >= dt, attr < next_month]
        if seq == "like":
            return [attr.like(f"%{val}%")]
        if seq == "in":
            if isinstance(val, (list, tuple, set)) and len(val) == 0:
                return [false()]  # 空集合查询 = 永假条件
            return [attr.in_(val)]
        if seq == "between" and isinstance(val, (list, tuple)) and len(val) == 2:
            return [attr.between(val[0], val[1])]

        method = _OPERATOR_MAP.get(seq)
        if method is not None:
            return [getattr(attr, method)(val)]
        return []

    def _parse_order(self, order: list[dict[str, str]]) -> list[ColumnElement]:
        """`[{"field": "asc|desc"}, ...]` → SQLAlchemy order_by 子句。

        非法字段直接跳过（前端传错排序字段不应 500）；全部无效时退回主键倒序，
        确保分页结果始终有序。``direction`` 大小写与空值均做了兜底。
        """
        columns: list[ColumnElement] = []
        for item in order:
            for field, direction in item.items():
                if field not in self.model.__table__.columns:
                    continue
                column = getattr(self.model, field)  # type: ignore[arg-type]
                columns.append(desc(column) if str(direction).lower() == "desc" else asc(column))
        return columns or [desc(self._get_pk_col())]

    def _loader_options(self, preload: list[str | Any] | None = None) -> list[Any]:
        """将字符串预加载描述转为 SQLAlchemy loading options。

        - "user"       → joinedload（一对一/多对一）或 selectinload（一对多/多对多）
        - "user.dept"  → 嵌套加载（通过 .options() 链式组合）
        - 已存在 options 对象 → 原样追加

        自动为 created_by / updated_by 添加 joinedload（所有查询都 LEFT JOIN 用户表获取审计人）。
        """
        options: list[Any] = []
        if not preload:
            preload = []
        mapper = sa_inspect(self.model)
        processed_attrs = set()

        for opt in preload:
            if isinstance(opt, str):
                parts = opt.split(".")
                if len(parts) == 1:
                    attr_name = parts[0]
                    if attr_name in processed_attrs:
                        continue  # 跳过同层重复名称
                    processed_attrs.add(attr_name)
                    if not hasattr(self.model, attr_name):
                        continue  # 非模型属性，忽略
                    prop = mapper.relationships.get(attr_name)
                    if prop is None:
                        continue  # 列属性不支持 eager loading，忽略
                    attr = getattr(self.model, attr_name)
                    # 一对一/多对一用 joinedload（一条 SQL 完成），一对多/多对多用 selectinload（N+1 → 2 条 SQL）
                    if not prop.uselist:
                        options.append(joinedload(attr))
                    else:
                        options.append(selectinload(attr))
                else:
                    full_path = ".".join(parts)
                    if full_path in processed_attrs:
                        continue  # 跳过完全相同的嵌套路径
                    processed_attrs.add(full_path)
                    current_model = self.model
                    current_mapper = mapper
                    current_option = None
                    for part in parts:
                        if not hasattr(current_model, part):
                            break
                        attr = getattr(current_model, part)
                        prop = current_mapper.relationships.get(part)
                        if prop is None:
                            break  # 非关系属性中断链
                        loader = selectinload(attr) if prop.uselist else joinedload(attr)
                        # 嵌套加载通过 .options() 链式组合
                        if current_option is None:
                            current_option = loader
                        else:
                            current_option = current_option.options(loader)
                        current_model = prop.mapper.class_
                        current_mapper = sa_inspect(current_model)
                    if current_option is not None:
                        options.append(current_option)
            else:
                options.append(opt)

        # 自动预加载审计关系：常见列表页都要展示创建人/更新人，统一处理避免 N+1
        for audit_attr in ["created_by", "updated_by"]:
            if audit_attr not in processed_attrs and audit_attr in mapper.relationships:
                prop = mapper.relationships[audit_attr]
                if hasattr(self.model, audit_attr):
                    attr = getattr(self.model, audit_attr)
                    options.append(joinedload(attr))

        return options
