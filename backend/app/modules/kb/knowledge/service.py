"""知识维护（管理端）服务：业务规则校验、级联事务与跨子模块协调。

分层约定：
- Service 只编排 ``KbKnowledgeCRUD`` 与其它子模块的 CRUD，不写 ORM 之外的 SQL；
- 事务边界在 HTTP 层（``db_getter``），本层只做业务判定与调用 CRUD；
- 发布单元不存在 / 业务规则不满足一律抛 ``CustomException``（默认 400，404 显式钉死）。
"""

from typing import Any

from fastapi import status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_schema import PageResultSchema
from app.core.exceptions import CustomException
from app.utils.common_util import search_to_dict

from ..chunk.crud import KbChunkCRUD
from ..entry_meta.model import KbEntryMetaModel
from ..publication.crud import KbPublicationCRUD
from ..publication.model import KbPublicationModel
from ..revision.crud import KbRevisionCRUD
from .crud import KbKnowledgeCRUD
from .schema import (
    KbKnowledgeChunkDetailOutSchema,
    KbKnowledgeChunkOutSchema,
    KbKnowledgeCreateSchema,
    KbKnowledgeDetailOutSchema,
    KbKnowledgeOutSchema,
    KbKnowledgeQueryParam,
    KbKnowledgeRevisionDetailOutSchema,
    KbKnowledgeRevisionOutSchema,
    KbKnowledgeStatusSchema,
    KbKnowledgeUpdateSchema,
)


class KbKnowledgeService:
    """知识条目管理服务（列表 / 详情 / 新增 / 编辑 / 状态 / 删除 / 版本 / 切片）。"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.crud = KbKnowledgeCRUD(db)

    # ── 查询 ──────────────────────────────────────────────────────────

    async def page(
        self,
        *,
        page_no: int,
        page_size: int,
        search: KbKnowledgeQueryParam | None = None,
        order_by: list[dict[str, str]] | None = None,
    ) -> PageResultSchema[KbKnowledgeOutSchema]:
        """分页查询：元数据 outer join 发布单元，支持标题/分类/状态/类型过滤。"""
        rows, total = await self.crud.page_with_publication(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=order_by,
            search=search_to_dict(search),
            status=search.status if search else None,
            resource_type=search.resource_type if search else None,
        )
        items = [KbKnowledgeOutSchema(**self._merge_out(meta, publication)) for meta, publication in rows]
        return PageResultSchema(
            page_no=page_no,
            page_size=page_size,
            total=total,
            has_next=page_no * page_size < total,
            items=items,
        )

    async def detail(self, publication_id: str) -> KbKnowledgeDetailOutSchema:
        """详情：元数据 + 发布指针 + 版本数/切片数。"""
        found = await self.crud.get_with_publication(publication_id)
        if found is None:
            raise CustomException(msg="知识条目不存在", status_code=status.HTTP_404_NOT_FOUND)
        meta, publication = found
        return KbKnowledgeDetailOutSchema(
            **self._merge_out(meta, publication),
            version_count=await self.crud.count_versions(publication_id),
            chunk_count=await self.crud.count_chunks(publication_id),
        )

    async def list_revisions(
        self,
        *,
        publication_id: str,
        page_no: int,
        page_size: int,
        order_by: list[dict[str, str]] | None = None,
    ) -> PageResultSchema[KbKnowledgeRevisionOutSchema]:
        """版本历史分页：校验发布单元存在后按版本号倒序。"""
        await self._ensure_publication(publication_id)
        result = await KbRevisionCRUD(self.db).page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=order_by or [{"revision": "desc"}],
            search={"publication_id": publication_id},
            out_schema=KbKnowledgeRevisionOutSchema,
        )
        return result

    async def detail_revision(self, revision_id: int) -> KbKnowledgeRevisionDetailOutSchema:
        """版本详情（含契约透传元数据）。"""
        return await KbRevisionCRUD(self.db).get_or_404(
            id=revision_id,
            msg="版本快照不存在",
            out_schema=KbKnowledgeRevisionDetailOutSchema,
        )

    async def list_chunks(
        self,
        *,
        publication_id: str,
        record_id: str | None = None,
        page_no: int,
        page_size: int,
        order_by: list[dict[str, str]] | None = None,
    ) -> PageResultSchema[KbKnowledgeChunkOutSchema]:
        """切片分页：可传 record_id 只看某版本快照。"""
        await self._ensure_publication(publication_id)
        search: dict[str, Any] = {"publication_id": publication_id}
        if record_id:
            search["record_id"] = record_id
        return await KbChunkCRUD(self.db).page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=order_by or [{"seq": "asc"}],
            search=search,
            out_schema=KbKnowledgeChunkOutSchema,
        )

    async def detail_chunk(self, chunk_id: int) -> KbKnowledgeChunkDetailOutSchema:
        """切片详情（管理端只读查看正文）。"""
        return await KbChunkCRUD(self.db).get_or_404(
            id=chunk_id,
            msg="知识切片不存在",
            out_schema=KbKnowledgeChunkDetailOutSchema,
        )

    # ── 写入 ──────────────────────────────────────────────────────────

    async def create(self, data: KbKnowledgeCreateSchema) -> KbKnowledgeDetailOutSchema:
        """新增知识条目：元数据 + 发布单元同事务创建。

        业务校验：
        - ``publication_id`` 全局唯一，重复创建直接 409（先查后写只是提前报错，真正的互斥由唯一键兜底）；
        - ``resource_type`` 由 Schema 的 Literal 白名单兜住，这里不再重复校验。
        """
        pub_crud = KbPublicationCRUD(self.db)
        existing = await pub_crud.get(publication_id=data.publication_id)
        if existing is not None:
            raise CustomException(
                msg=f"发布单元 {data.publication_id} 已存在，不能重复创建",
                status_code=status.HTTP_409_CONFLICT,
            )

        await pub_crud.create(
            data={
                "publication_id": data.publication_id,
                "tenant_id": data.tenant_id,
                "resource_id": data.resource_id,
                "resource_type": data.resource_type,
                "current_record_id": None,
                "current_revision": 0,
                "last_synced_at": None,
                "status": "active",
            }
        )
        await self.crud.create(
            data={
                "publication_id": data.publication_id,
                "title": data.title,
                "category": data.category,
                "summary": data.summary,
                "tags": data.tags,
                "owner": data.owner,
            }
        )
        return await self.detail(data.publication_id)

    async def update(
        self, publication_id: str, data: KbKnowledgeUpdateSchema
    ) -> KbKnowledgeDetailOutSchema:
        """编辑知识条目：元数据与归属信息分开更新（显式传 null 表示清空可空字段）。"""
        await self._ensure_publication(publication_id)

        # exclude_unset 保留调用方显式传入的 null（清空语义），未传的字段不参与更新
        values = data.model_dump(exclude_unset=True)
        meta_values = {key: value for key, value in values.items() if key in ("title", "category", "summary", "tags", "owner")}
        pub_values = {key: value for key, value in values.items() if key in ("tenant_id", "resource_id", "resource_type")}

        if meta_values:
            await self.crud.update_meta_fields(publication_id=publication_id, values=meta_values)
        if pub_values:
            await self.crud.update_publication_fields(publication_id=publication_id, values=pub_values)
        return await self.detail(publication_id)

    async def update_status(self, publication_id: str, data: KbKnowledgeStatusSchema) -> KbKnowledgeDetailOutSchema:
        """上下线：只改发布单元状态（active/inactive/deleted），不动内容与版本。"""
        await self._ensure_publication(publication_id)
        await self.crud.update_publication_fields(publication_id=publication_id, values={"status": data.status})
        return await self.detail(publication_id)

    async def delete(self, publication_id: str) -> None:
        """整单删除：物理删除切片、版本、元数据与发布单元（不可恢复）。"""
        await self._ensure_publication(publication_id)
        await self.crud.delete_cascade(publication_id=publication_id)

    # ── 私有 ──────────────────────────────────────────────────────────

    async def _ensure_publication(self, publication_id: str) -> KbPublicationModel:
        """校验发布单元存在，否则 404（详情/编辑/删除/版本/切片共用）。"""
        publication = await KbPublicationCRUD(self.db).get(publication_id=publication_id)
        if publication is None:
            raise CustomException(msg="知识条目不存在", status_code=status.HTTP_404_NOT_FOUND)
        return publication

    @staticmethod
    def _merge_out(
        meta: KbEntryMetaModel | None, publication: KbPublicationModel | None
    ) -> dict[str, Any]:
        """把元数据行与发布单元行合并成响应字段字典（任一侧缺失用 None 兜底）。"""
        return {
            "id": meta.id if meta else None,
            "uuid": meta.uuid if meta else None,
            "created_time": meta.created_time if meta else None,
            "updated_time": meta.updated_time if meta else None,
            "publication_id": meta.publication_id if meta else (publication.publication_id if publication else ""),
            "title": meta.title if meta else "",
            "category": meta.category if meta else None,
            "summary": meta.summary if meta else None,
            "tags": meta.tags if meta else [],
            "owner": meta.owner if meta else None,
            "resource_type": publication.resource_type if publication else None,
            "tenant_id": publication.tenant_id if publication else None,
            "resource_id": publication.resource_id if publication else None,
            "current_record_id": publication.current_record_id if publication else None,
            "current_revision": publication.current_revision if publication else None,
            "last_synced_at": publication.last_synced_at if publication else None,
            "status": publication.status if publication else None,
        }
