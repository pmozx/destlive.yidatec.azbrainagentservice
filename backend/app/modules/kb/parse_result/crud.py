"""解析产物数据层。

本工程没有迁移工具：``pages`` / ``suggested_doc_type`` 是 T2.4 新增列，已存在的表需要
启动/首次访问时幂等补列，否则 ORM 生成的 SELECT 会因缺列直接失败。补列语句都是
``ADD COLUMN IF NOT EXISTS``，并发重复执行也安全。
"""

from typing import Any

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_crud import CRUDBase
from app.core.database import async_engine
from app.core.logger import logger

from .model import KbParseResultModel

#: 当前进程是否已补列（多进程各自执行一次，DDL 本身幂等）
_SCHEMA_READY = False

_EXTRA_COLUMNS: tuple[str, ...] = (
    "ALTER TABLE sbp_ai_kb_parse_result ADD COLUMN IF NOT EXISTS pages JSONB NOT NULL DEFAULT '[]'::jsonb",
    "ALTER TABLE sbp_ai_kb_parse_result ADD COLUMN IF NOT EXISTS suggested_doc_type VARCHAR(64)",
)


async def ensure_parse_result_schema() -> None:
    """幂等补齐解析产物表的 T2.4 列（表不存在时交给建表流程，这里只告警）。"""
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    try:
        async with async_engine.begin() as conn:
            for statement in _EXTRA_COLUMNS:
                await conn.execute(text(statement))
    except Exception as exc:  # noqa: BLE001 建表失败/库不可用时由实际读写给出可读错误
        logger.warning("解析产物表补列失败（pages / suggested_doc_type 可能不可用）: {}", exc)
        return
    _SCHEMA_READY = True


class KbParseResultCRUD(CRUDBase[KbParseResultModel, BaseModel, BaseModel]):
    """解析产物数据层（不可变，一般不提供更新入口）。"""

    def __init__(self, db: AsyncSession) -> None:
        super().__init__(model=KbParseResultModel, db=db)

    async def get(self, preload: list[str | Any] | None = None, **kwargs: Any) -> KbParseResultModel | None:
        """读取前确保补列已完成（管理端与契约链路都走这里）。"""
        await ensure_parse_result_schema()
        return await super().get(preload=preload, **kwargs)

    async def create(self, data: BaseModel | dict[str, Any]) -> KbParseResultModel:
        """写入前确保补列已完成。"""
        await ensure_parse_result_schema()
        return await super().create(data=data)
