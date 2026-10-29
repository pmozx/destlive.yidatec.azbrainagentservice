from typing import Any

from pydantic import BaseModel, Field

from app.core.base_schema import BaseQueryParam, BaseSchema


class ChunkOutSchema(BaseSchema):
    """分片响应模型（含向量化后的原文与溯源信息）"""

    space_id: int = Field(default=0, description="知识空间ID")
    doc_id: int = Field(default=0, description="文档ID")
    chunk_index: int = Field(default=0, description="分片序号(文档内从0递增)")
    content: str = Field(default="", description="分片原文")
    page_no: int | None = Field(default=None, description="原文页码")
    section_title: str | None = Field(default=None, description="所属标题链")
    token_count: int = Field(default=0, description="估算字符数")
    meta: dict[str, Any] = Field(default_factory=dict, description="解析器附加信息")


class ChunkQueryParam(BaseQueryParam):
    """分片查询参数"""

    space_id: int | None = Field(None, ge=1, description="知识空间ID", json_schema_extra={"q": "eq"})
    doc_id: int | None = Field(None, ge=1, description="文档ID", json_schema_extra={"q": "eq"})
    content: str | None = Field(None, description="分片内容", json_schema_extra={"q": "like"})


class ChunkBatchDeleteSchema(BaseModel):
    """按主键批量删除分片参数（人工清理脏数据）"""

    ids: list[int] = Field(..., min_length=1, description="分片ID列表")
