from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_crud import CRUDBase

from .model import IntegrationKbDocumentModel
from .schema import DocumentUpdateSchema


class IntegrationKbDocumentCRUD(CRUDBase[IntegrationKbDocumentModel, DocumentUpdateSchema, DocumentUpdateSchema]):
    """知识文档数据层"""

    def __init__(self, db: AsyncSession) -> None:
        super().__init__(model=IntegrationKbDocumentModel, db=db)

    async def get_by_md5(self, space_id: int, file_md5: str) -> IntegrationKbDocumentModel | None:
        """空间内按 MD5 查找未删除文档（重复上传直接拒绝，避免同一份文件占两份向量）。"""
        return await self.get(space_id=space_id, file_md5=file_md5)

    async def increment_ref_count(self, doc_ids: list[int]) -> None:
        """累加被引用次数（问答命中引用时调用，用于「文档引用排行」）。"""
        if not doc_ids:
            return
        await self.db.execute(
            update(IntegrationKbDocumentModel)
            .where(IntegrationKbDocumentModel.id.in_(set(doc_ids)))
            .values(ref_count=IntegrationKbDocumentModel.ref_count + 1)
        )
