from sqlalchemy.ext.asyncio import AsyncSession

from app.config.setting import settings
from app.core.vector_store import (
    VECTOR_INDEX_NAME,
    VECTOR_TABLE_NAME,
    count_chunks,
    delete_chunks,
    search_chunks,
    upsert_chunks,
    vector_store_ready,
)

from .schema import (
    ChunkDeleteSchema,
    ChunkSearchSchema,
    ChunkUpsertResultSchema,
    ChunkUpsertSchema,
    VectorStatusSchema,
)


class VectorStoreService:
    """向量存储服务：仅做参数编排，读写细节在 app/core/vector_store.py"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def upsert(self, data: ChunkUpsertSchema) -> ChunkUpsertResultSchema:
        """批量写入切块（幂等覆盖）"""
        written = await upsert_chunks(self.db, [item.model_dump() for item in data.chunks])
        return ChunkUpsertResultSchema(written=written)

    async def search(self, data: ChunkSearchSchema) -> list[dict]:
        """按余弦相似度召回切块"""
        return await search_chunks(
            self.db,
            data.query_embedding,
            space_id=data.space_id,
            doc_id=data.doc_id,
            top_k=data.top_k,
            min_score=data.min_score,
        )

    async def delete(self, data: ChunkDeleteSchema) -> int:
        """删除指定文档的全部切块"""
        return await delete_chunks(self.db, doc_id=data.doc_id, space_id=data.space_id)

    async def status(self) -> VectorStatusSchema:
        """自检：配置是否启用、表是否可查、当前切块总量"""
        enabled = vector_store_ready()
        info = {
            "enabled": enabled,
            "database_type": settings.DATABASE_TYPE,
            "dim": settings.VECTOR_DIM,
            "table": VECTOR_TABLE_NAME,
            "index": VECTOR_INDEX_NAME,
            "chunk_total": None,
            "message": None,
        }
        if not enabled:
            info["message"] = "向量能力未启用：需要 DATABASE_TYPE=postgres 且 VECTOR_ENABLE=true"
            return VectorStatusSchema(**info)
        try:
            info["chunk_total"] = await count_chunks(self.db)
        except Exception as e:
            # 表/扩展缺失属于部署问题，状态接口降级返回原因而不是直接 500
            info["message"] = f"向量表不可用: {e!s}"
        return VectorStatusSchema(**info)
