"""知识切片数据层：新增批量写入、按快照删除/计数（写入链路专用）。"""

from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel
from sqlalchemy import delete, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_crud import CRUDBase

from .model import KbChunkModel


class KbChunkCRUD(CRUDBase[KbChunkModel, BaseModel, BaseModel]):
    """知识切片数据层。"""

    def __init__(self, db: AsyncSession) -> None:
        super().__init__(model=KbChunkModel, db=db)

    async def insert_many(self, rows: Sequence[dict[str, Any]]) -> int:
        """批量写入切片（含向量）：整批一条 INSERT，失败整批回滚，不允许半成品。"""
        if not rows:
            return 0
        await self.db.execute(insert(KbChunkModel).values(list(rows)))
        return len(rows)

    async def delete_by_record(self, *, publication_id: str, record_id: str) -> int:
        """物理删除某快照的全部分片（#06 删除后必须不可召回，软删向量仍会被检索）。"""
        result = await self.db.execute(
            delete(KbChunkModel).where(
                KbChunkModel.publication_id == publication_id,
                KbChunkModel.record_id == record_id,
            )
        )
        return int(getattr(result, "rowcount", 0) or 0)

    async def count_by_record(self, *, publication_id: str, record_id: str) -> int:
        """统计某快照的分片数（验收：向量条数 = 切片条数）。"""
        result = await self.db.execute(
            select(func.count())
            .select_from(KbChunkModel)
            .where(
                KbChunkModel.publication_id == publication_id,
                KbChunkModel.record_id == record_id,
            )
        )
        return int(result.scalar_one())

    async def page_by_record(
        self,
        *,
        publication_id: str,
        record_id: str,
        after_seq: int = 0,
        limit: int = 100,
    ) -> list[KbChunkModel]:
        """按 ``seq`` 升序读取某快照的切片（03.2 基线分页：``seq > after_seq``）。"""
        result = await self.db.execute(
            select(KbChunkModel)
            .where(
                KbChunkModel.publication_id == publication_id,
                KbChunkModel.record_id == record_id,
                KbChunkModel.seq > after_seq,
            )
            .order_by(KbChunkModel.seq)
            .limit(limit + 1)
        )
        return list(result.scalars().all())

    async def fetch_foreign_ids(self, *, publication_id: str, chunk_ids: Sequence[str]) -> set[str]:
        """返回属于其它发布单元的切片 ID（03.1 / 03.3 归属校验：不能引用别人的切片）。"""
        if not chunk_ids:
            return set()
        result = await self.db.execute(
            select(KbChunkModel.chunk_id).where(
                KbChunkModel.publication_id != publication_id,
                KbChunkModel.chunk_id.in_(list(chunk_ids)),
            )
        )
        return {row[0] for row in result.all() if row[0]}
