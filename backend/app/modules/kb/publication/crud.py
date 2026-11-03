"""发布单元数据层：单表 CRUD + CAS 指针推进 + 级联删除。

合并原 ``knowledge`` 子模块后，本文件统一承载：
- 基础 CRUD（继承 ``CRUDBase``）；
- CAS 指针推进（``advance``）：以 ``current_revision`` 做条件更新，防并发覆盖；
- 级联删除（``delete_cascade``）：切片 → 版本 → 发布单元，按依赖顺序物理删除；
- 关联统计（``count_versions`` / ``count_chunks``）。
"""

from typing import Any

from pydantic import BaseModel
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_crud import CRUDBase

from ..chunk.model import KbChunkModel
from .model import KbPublicationModel
from ..revision.model import KbRevisionModel


class KbPublicationCRUD(CRUDBase[KbPublicationModel, BaseModel, BaseModel]):
    """发布单元数据层（基础 CRUD + CAS 指针推进）。"""

    def __init__(self, db: AsyncSession) -> None:
        super().__init__(model=KbPublicationModel, db=db)

    async def advance(
        self,
        *,
        publication_id: str,
        expected_revision: int,
        values: dict[str, Any],
    ) -> bool:
        """按 ``current_revision`` 做条件更新（CAS），返回是否命中该行。

        修订与删除都是「读当前指针 → 校验 → 写新指针」，两步之间没有锁：
        两个并发请求都能通过校验，后写的那个会把 revision 覆盖回去，破坏单调性
        （进而让墓碑防复活失效）。把期望版本写进 WHERE 后，落败的一方 rowcount=0，
        由 Service 转成 409 ``STALE_REVISION``，语义与「基线已被他人修改」一致。

        注意：``expected_revision`` 是**校验通过时读到的版本**，不是请求里的新版本。
        """
        result = await self.db.execute(
            update(KbPublicationModel)
            .where(
                KbPublicationModel.publication_id == publication_id,
                KbPublicationModel.current_revision == expected_revision,
            )
            .values(**values)
        )
        return result.rowcount == 1

    async def count_versions(self, publication_id: str) -> int:
        """统计发布单元的版本快照数。"""
        result = await self.db.execute(
            select(func.count())
            .select_from(KbRevisionModel)
            .where(KbRevisionModel.publication_id == publication_id)
        )
        return int(result.scalar() or 0)

    async def count_chunks(self, publication_id: str) -> int:
        """统计发布单元的切片总数。"""
        result = await self.db.execute(
            select(func.count())
            .select_from(KbChunkModel)
            .where(KbChunkModel.publication_id == publication_id)
        )
        return int(result.scalar() or 0)

    async def delete_cascade(self, *, publication_id: str) -> None:
        """整单物理删除：切片 → 版本 → 发布单元。

        ``sbp_ai_kb_chunk`` 必须物理删（软删的向量仍会被检索召回）。
        """
        for model in (KbChunkModel, KbRevisionModel, KbPublicationModel):
            await self.db.execute(delete(model).where(model.publication_id == publication_id))
        await self.db.flush()
