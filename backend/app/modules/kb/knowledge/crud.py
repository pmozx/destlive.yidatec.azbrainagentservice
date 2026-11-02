"""知识维护数据层：发布单元 + 管理端元数据的组合查询，以及整单级联删除。

通用单表能力沿用 ``CRUDBase``；这里只补知识平台专属的两类操作：
1. **联表分页**：列表要同时展示元数据（标题/分类）与发布指针（状态/版本），单表 CRUD
   无法按发布单元字段过滤，因此在数据层显式 ``outer join``；
2. **级联删除**：物理删除切片 → 版本 → 元数据 → 发布单元，顺序与外键语义一致
   （切片/版本都引用发布单元，先删子行再删父行，避免中间态悬挂）。
"""

from typing import Any

from pydantic import BaseModel
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.core.base_crud import CRUDBase

from ..chunk.model import KbChunkModel
from ..entry_meta.model import KbEntryMetaModel
from ..publication.model import KbPublicationModel
from ..revision.model import KbRevisionModel


class KbKnowledgeCRUD(CRUDBase[KbEntryMetaModel, BaseModel, BaseModel]):
    """知识维护数据层（以 ``KbEntryMetaModel`` 为基座模型）。"""

    def __init__(self, db: AsyncSession) -> None:
        super().__init__(model=KbEntryMetaModel, db=db)

    async def page_with_publication(
        self,
        *,
        offset: int,
        limit: int,
        order_by: list[dict[str, str]] | None = None,
        search: dict[str, Any] | None = None,
        status: str | None = None,
        resource_type: str | None = None,
    ) -> tuple[list[tuple[KbEntryMetaModel, KbPublicationModel | None]], int]:
        """联表分页：返回 ``(元数据行, 发布单元行)`` 与总数。

        元数据字段走 ``search_to_dict`` 的通用条件；发布单元的状态/类型过滤需要显式 JOIN，
        因此作为独立入参从 Service 传下来（``status`` / ``resource_type`` 在 meta 上不存在，
        通用条件会自动跳过，不会误筛）。
        """
        conditions: list[ColumnElement] = await self._build_conditions(**(search or {}))
        if status:
            conditions.append(KbPublicationModel.status == status)
        if resource_type:
            conditions.append(KbPublicationModel.resource_type == resource_type)

        join_condition = KbEntryMetaModel.publication_id == KbPublicationModel.publication_id
        count_sql = (
            select(func.count(KbEntryMetaModel.id))
            .select_from(KbEntryMetaModel)
            .outerjoin(KbPublicationModel, join_condition)
            .where(*conditions)
        )
        total = int((await self.db.execute(count_sql)).scalar() or 0)

        data_sql = (
            select(KbEntryMetaModel, KbPublicationModel)
            .outerjoin(KbPublicationModel, join_condition)
            .where(*conditions)
            .order_by(*self._parse_order(order_by or [{"created_time": "desc"}]))
            .offset(offset)
            .limit(limit)
        )
        rows = (await self.db.execute(data_sql)).all()
        return [(row[0], row[1]) for row in rows], total

    async def get_with_publication(
        self, publication_id: str
    ) -> tuple[KbEntryMetaModel, KbPublicationModel | None] | None:
        """按发布单元 ID 取元数据与其发布指针，任一侧不存在都返回 None（由 Service 判 404）。"""
        result = await self.db.execute(
            select(KbEntryMetaModel, KbPublicationModel)
            .outerjoin(
                KbPublicationModel,
                KbEntryMetaModel.publication_id == KbPublicationModel.publication_id,
            )
            .where(KbEntryMetaModel.publication_id == publication_id)
        )
        row = result.first()
        return (row[0], row[1]) if row else None

    async def update_meta_fields(self, *, publication_id: str, values: dict[str, Any]) -> int:
        """按发布单元 ID 更新元数据（允许显式 null 清空可空字段），返回命中行数。"""
        if not values:
            return 0
        result = await self.db.execute(
            update(KbEntryMetaModel)
            .where(KbEntryMetaModel.publication_id == publication_id)
            .values(**values)
        )
        await self.db.flush()
        return int(getattr(result, "rowcount", 0) or 0)

    async def update_publication_fields(self, *, publication_id: str, values: dict[str, Any]) -> int:
        """按发布单元 ID 更新归属信息（租户/资源/类型），返回命中行数。"""
        if not values:
            return 0
        result = await self.db.execute(
            update(KbPublicationModel)
            .where(KbPublicationModel.publication_id == publication_id)
            .values(**values)
        )
        await self.db.flush()
        return int(getattr(result, "rowcount", 0) or 0)

    async def delete_cascade(self, *, publication_id: str) -> None:
        """整单物理删除：切片 → 版本 → 元数据 → 发布单元。

        ``sbp_ai_kb_chunk`` 必须物理删（软删的向量仍会被检索召回）。
        """
        for model in (KbChunkModel, KbRevisionModel, KbEntryMetaModel, KbPublicationModel):
            await self.db.execute(delete(model).where(model.publication_id == publication_id))
        await self.db.flush()

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
