"""解析产物的入库 Schema（产物不可变，只提供新增）。"""

from typing import Any

from pydantic import BaseModel, Field


class KbParseResultCreateSchema(BaseModel):
    """一次解析的完整产物：切片、建议、告警与整体哈希。"""

    job_id: str = Field(description="parse_job.uuid")
    chunks: list[dict[str, Any]] = Field(default_factory=list, description="切片数组（不含向量）")
    pages: list[dict[str, Any]] = Field(default_factory=list, description="页图预览（无能力时为空）")
    suggested: dict[str, Any] | None = Field(default=None, description="AI 建议元数据")
    suggested_doc_type: str | None = Field(default=None, description="建议文档分类")
    warnings: list[str] = Field(default_factory=list, description="非致命解析告警")
    content_hash: str = Field(description="产物整体哈希（版本比对）")
