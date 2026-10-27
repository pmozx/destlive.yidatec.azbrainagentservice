"""向量存储接口的请求/响应模型。

embedding 由调用方（业务层或外部服务）生成后传入，本模块只负责维度校验与存取，
不绑定具体模型供应商。
"""

from typing import Any

from pydantic import BaseModel, Field


class ChunkUpsertItemSchema(BaseModel):
    """单个切块写入模型"""

    space_id: str = Field(..., min_length=1, max_length=64, description="知识空间ID")
    doc_id: str = Field(..., min_length=1, max_length=64, description="文档ID")
    chunk_index: int = Field(..., ge=0, description="切块序号（同文档内从 0 递增）")
    content: str = Field(..., min_length=1, description="切块原文")
    embedding: list[float] = Field(..., min_length=1, description="向量（维度须与 VECTOR_DIM 一致）")
    page_no: int | None = Field(default=None, ge=1, description="原文页码")
    meta: dict[str, Any] = Field(default_factory=dict, description="业务附加信息（标题、来源、分类等）")


class ChunkUpsertSchema(BaseModel):
    """批量写入模型：按 (space_id, doc_id, chunk_index) 幂等覆盖，建议每批 ≤32 条"""

    chunks: list[ChunkUpsertItemSchema] = Field(..., min_length=1, max_length=200, description="切块列表")


class ChunkSearchSchema(BaseModel):
    """相似度检索参数"""

    query_embedding: list[float] = Field(..., min_length=1, description="查询向量")
    space_id: str | None = Field(default=None, max_length=64, description="限定知识空间")
    doc_id: str | None = Field(default=None, max_length=64, description="限定文档")
    top_k: int = Field(default=10, ge=1, le=100, description="召回条数")
    min_score: float | None = Field(
        default=None,
        ge=-1.0,
        le=1.0,
        description="相似度下限（score = 1 - 余弦距离，越大越相似），低于该值的命中被丢弃",
    )


class ChunkHitSchema(BaseModel):
    """单条召回结果"""

    id: int = Field(..., description="切块主键")
    space_id: str = Field(..., description="知识空间ID")
    doc_id: str = Field(..., description="文档ID")
    chunk_index: int = Field(..., description="切块序号")
    page_no: int | None = Field(default=None, description="原文页码")
    content: str = Field(..., description="切块原文")
    meta: dict[str, Any] = Field(default_factory=dict, description="业务附加信息")
    score: float = Field(..., description="余弦相似度（1 - 余弦距离）")


class ChunkDeleteSchema(BaseModel):
    """按文档删除切块参数"""

    doc_id: str = Field(..., min_length=1, max_length=64, description="文档ID")
    space_id: str | None = Field(default=None, max_length=64, description="限定知识空间（同 doc_id 可跨空间复用）")


class ChunkUpsertResultSchema(BaseModel):
    """写入结果"""

    written: int = Field(..., ge=0, description="本次写入（含覆盖）条数")


class VectorStatusSchema(BaseModel):
    """向量存储自检结果"""

    enabled: bool = Field(..., description="向量能力是否启用（postgres + VECTOR_ENABLE）")
    database_type: str = Field(..., description="当前数据库类型")
    dim: int = Field(..., description="配置的向量维度")
    table: str = Field(..., description="切块表名")
    index: str = Field(..., description="向量索引名")
    chunk_total: int | None = Field(default=None, description="切块总数，向量表不可用时为 null")
    message: str | None = Field(default=None, description="不可用原因")
