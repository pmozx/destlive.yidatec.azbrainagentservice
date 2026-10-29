from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_crud import CRUDBase

from .model import IntegrationKbChunkModel
from .schema import ChunkQueryParam


class IntegrationKbChunkCRUD(CRUDBase[IntegrationKbChunkModel, ChunkQueryParam, ChunkQueryParam]):
    """知识分片数据层（只读浏览 + 人工清理；写入统一走同目录 ``vector_store``）"""

    def __init__(self, db: AsyncSession) -> None:
        super().__init__(model=IntegrationKbChunkModel, db=db)
