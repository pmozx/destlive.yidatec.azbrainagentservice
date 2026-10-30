"""知识空间服务：空间 CRUD + 删除级联（分类 / 文档 / 分片 / 对象存储）。"""

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_schema import PageResultSchema
from app.core.exceptions import CustomException
from app.core.logger import logger
from app.utils.common_util import search_to_dict

from ..category.model import IntegrationKbCategoryModel
from ..chunk.vector_store import delete_chunks, vector_store_ready
from ..document.model import IntegrationKbDocumentModel
from .crud import IntegrationKbSpaceCRUD
from .schema import SpaceCreateSchema, SpaceOutSchema, SpaceQueryParam, SpaceUpdateSchema


class IntegrationKbSpaceService:
    """知识空间服务（无 RBAC：调用方即管理端）"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.crud = IntegrationKbSpaceCRUD(db)

    async def page(
        self,
        page_no: int,
        page_size: int,
        search: SpaceQueryParam | None = None,
        order_by: list[dict[str, str]] | None = None,
    ) -> PageResultSchema[SpaceOutSchema]:
        """分页查询空间：默认按 sort 升序、ID 升序。"""
        return await self.crud.page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=order_by or [{"sort": "asc"}, {"id": "asc"}],
            search=search_to_dict(search),
            out_schema=SpaceOutSchema,
        )

    async def options(self) -> list[SpaceOutSchema]:
        """空间下拉选项（仅启用状态，供文档上传与问答范围选择器使用）。"""
        rows = await self.crud.get_list(
            search={"status": ("eq", 0)},
            order_by=[{"sort": "asc"}, {"id": "asc"}],
        )
        return [SpaceOutSchema.model_validate(row) for row in rows]

    async def detail(self, id: int) -> SpaceOutSchema:
        """空间详情（doc_count 按未删除文档实时统计，避免冗余计数漂移）。"""
        obj = await self.crud.get_or_404(id=id, msg="知识空间不存在")
        result = SpaceOutSchema.model_validate(obj)
        result.doc_count = await self._doc_count(id)
        return result

    async def create(self, data: SpaceCreateSchema) -> SpaceOutSchema:
        """创建空间（code 唯一）。"""
        if await self.crud.exists(code=data.code):
            raise CustomException(msg=f"空间编码已存在：{data.code}")
        obj = await self.crud.create(data=data)
        return SpaceOutSchema.model_validate(obj)

    async def update(self, id: int, data: SpaceUpdateSchema) -> SpaceOutSchema:
        """修改空间（编码不可改，改它需级联改文档与分片）。"""
        await self.crud.get_or_404(id=id, msg="修改失败，知识空间不存在")
        updated = await self.crud.update(id=id, data=data)
        return SpaceOutSchema.model_validate(updated)

    async def delete(self, ids: list[int]) -> None:
        """删除空间：级联软删分类与文档，物理清空分片，并清理对象存储产物与原件。

        分片必须物理删除：软删后向量仍在表里，问答仍会召回「已删除空间」的内容。
        """
        if not ids:
            raise CustomException(msg="删除失败，删除对象不能为空")
        spaces = await self.crud.get_list(search={"id": ("in", ids)})
        if len(spaces) != len(set(ids)):
            raise CustomException(msg="删除失败，存在不存在的知识空间")

        documents = (
            await self.db.execute(
                select(IntegrationKbDocumentModel).where(
                    IntegrationKbDocumentModel.space_id.in_(ids),
                    IntegrationKbDocumentModel.is_deleted.is_(False),
                )
            )
        ).scalars().all()

        for document in documents:
            await self._purge_document_assets(document)

        if documents:
            await self.db.execute(
                update(IntegrationKbDocumentModel)
                .where(IntegrationKbDocumentModel.space_id.in_(ids), IntegrationKbDocumentModel.is_deleted.is_(False))
                .values(is_deleted=True, error_msg="所属知识空间已删除")
            )
        await self.db.execute(
            update(IntegrationKbCategoryModel)
            .where(IntegrationKbCategoryModel.space_id.in_(ids), IntegrationKbCategoryModel.is_deleted.is_(False))
            .values(is_deleted=True)
        )
        await self.crud.delete(ids=ids)

    async def _doc_count(self, space_id: int) -> int:
        result = await self.db.execute(
            select(func.count()).select_from(IntegrationKbDocumentModel).where(
                IntegrationKbDocumentModel.space_id == space_id,
                IntegrationKbDocumentModel.is_deleted.is_(False),
            )
        )
        return int(result.scalar() or 0)

    async def _purge_document_assets(self, document: IntegrationKbDocumentModel) -> None:
        """清理单文档的向量与对象存储目录（失败只告警：删除操作不能因对象存储不可用而半途中断）。"""
        if vector_store_ready():
            try:
                await delete_chunks(doc_ids=[document.id])
            except Exception:
                logger.warning("知识空间删除：清理分片失败（doc_id={}）", document.id, exc_info=True)
        try:
            from ..clients import S3StorageClient

            client = S3StorageClient()
            # 原件与解析产物同属「文档目录」，删目录即可一并清理（前缀为空时客户端会拒绝执行）
            if client.configured and document.object_key:
                await client.delete_prefix(f"{document.object_key.rsplit('/', 1)[0]}/")
        except Exception:
            logger.warning("知识空间删除：清理对象存储失败（doc_id={}）", document.id, exc_info=True)
