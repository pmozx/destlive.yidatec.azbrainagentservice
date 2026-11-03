"""解析任务的入库 / 更新 Schema（CRUD 的类型入口，不做契约字段校验）。"""

from typing import Any

from pydantic import BaseModel, Field

from app.core.base_schema import BaseQueryParam, BaseSchema
from app.core.validator import DateTimeStr


class KbParseJobCreateSchema(BaseModel):
    """新建解析任务：``uuid`` 即契约 ``job_id``（由 GSI 预生成）。"""

    uuid: str = Field(description="契约 job_id，也是任务行 uuid")
    resource_type: str = Field(description="document / faq")
    file_meta: dict[str, Any] = Field(default_factory=dict, description="原始请求快照（文件名 / 来源 / metadata）")


class KbParseJobUpdateSchema(BaseModel):
    """任务状态机更新（只更新实际传入的字段）。"""

    status: str | None = Field(default=None, description="queued / running / succeeded / failed")
    progress: int | None = Field(default=None, ge=0, le=100, description="进度 0–100")
    error_code: str | None = Field(default=None, description="任务态失败码")
    error_message: str | None = Field(default=None, description="任务态失败说明")


class KbParseResultOutSchema(BaseModel):
    """解析产物（#02 的结果快照，不含向量）。"""

    model_config = {"from_attributes": True}

    job_id: str = Field(..., description="parse_job.uuid")
    chunks: list[dict[str, Any]] = Field(default_factory=list, description="切片数组")
    pages: list[dict[str, Any]] = Field(default_factory=list, description="页图预览")
    suggested: dict[str, Any] | None = Field(default=None, description="AI 建议元数据")
    suggested_doc_type: str | None = Field(default=None, description="建议文档分类")
    warnings: list[Any] = Field(default_factory=list, description="解析告警")
    content_hash: str = Field(..., description="产物整体哈希")
    created_time: DateTimeStr | None = Field(default=None, description="产物写入时间")


class KbParseJobOutSchema(BaseSchema):
    """解析任务列表响应（不返回原始 file_meta，避免大字段进列表）。"""

    uuid: str = Field(..., description="契约 job_id")
    resource_type: str = Field(..., description="document/faq")
    status: str = Field(..., description="queued/running/succeeded/failed")
    progress: int = Field(default=0, description="进度 0–100")
    error_code: str | None = Field(default=None, description="失败码")
    error_message: str | None = Field(default=None, description="失败说明")


class KbParseJobDetailOutSchema(KbParseJobOutSchema):
    """解析任务详情响应（含原始请求快照与解析产物）。"""

    file_meta: dict[str, Any] = Field(default_factory=dict, description="原始请求快照")
    result: KbParseResultOutSchema | None = Field(default=None, description="解析产物（未完成时为 null）")


class KbParseJobQueryParam(BaseQueryParam):
    """解析任务查询参数。"""

    uuid: str | None = Field(None, description="任务 ID", json_schema_extra={"q": "like"})
    resource_type: str | None = Field(None, description="资源类型", json_schema_extra={"q": "eq"})
    status: str | None = Field(None, description="任务状态", json_schema_extra={"q": "eq"})
    error_code: str | None = Field(None, description="失败码", json_schema_extra={"q": "eq"})

