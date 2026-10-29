"""知识分片服务：按文档浏览分片、人工清理脏分片。

写入（分片 + 向量）由解析流水线负责（``pipeline/document_pipeline.py`` → ``chunk/vector_store.py``），
本层不提供「手工新增分片」：没有向量的分片永远检索不到，只会让统计口径失真。
"""

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_schema import PageResultSchema
from app.core.exceptions import CustomException
from app.utils.common_util import search_to_dict

from .crud import IntegrationKbChunkCRUD
from .model import IntegrationKbChunkModel
from .schema import ChunkOutSchema, ChunkQueryParam


class IntegrationKbChunkService:
    """知识分片服务"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.crud = IntegrationKbChunkCRUD(db)

    async def page(
        self,
        page_no: int,
        page_size: int,
        search: ChunkQueryParam | None = None,
        order_by: list[dict[str, str]] | None = None,
    ) -> PageResultSchema[ChunkOutSchema]:
        """分页查询分片：默认按 (文档, 序号) 升序，保证「原文顺序」可读。"""
        return await self.crud.page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=order_by or [{"doc_id": "asc"}, {"chunk_index": "asc"}],
            search=search_to_dict(search),
            out_schema=ChunkOutSchema,
        )

    async def delete(self, ids: list[int]) -> None:
        """按主键删除分片（物理删除：软删后向量仍在表里仍会被召回）。"""
        if not ids:
            raise CustomException(msg="删除失败，删除对象不能为空")
        await self.db.execute(delete(IntegrationKbChunkModel).where(IntegrationKbChunkModel.id.in_(set(ids))))
