"""解析任务的入库 / 更新 Schema（CRUD 的类型入口，不做契约字段校验）。"""

from typing import Any

from pydantic import BaseModel, Field


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
