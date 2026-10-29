import json

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.base_schema import BaseQueryParam, BaseSchema
from app.core.validator import DateTimeStr

from .model import STAGE_LABELS, TaskStatus, TaskType


class TaskCreateSchema(BaseModel):
    """任务创建参数（服务端内部调用：上传/重新解析时落台账，不开放为接口入参）"""

    task_type: int = Field(default=int(TaskType.PARSE), description="任务类型(1:文档解析)")
    space_id: int = Field(..., ge=1, description="所属空间ID")
    doc_id: int = Field(..., ge=1, description="关联文档ID")


class TaskStepCreateSchema(BaseModel):
    """步骤明细创建参数（服务端内部调用：登记任务时预置全部节点，状态为待执行）"""

    task_id: int = Field(..., ge=1, description="所属任务ID")
    doc_id: int = Field(..., ge=1, description="关联文档ID")
    space_id: int = Field(..., ge=1, description="所属空间ID")
    stage: int = Field(..., ge=1, le=4, description="节点(1:解析 2:分片 3:向量化 4:写入pgvector)")
    seq: int = Field(..., ge=1, description="节点序号")
    status: int = Field(default=int(TaskStatus.PENDING), description="状态(0:待执行 1:执行中 2:已完成 3:失败 4:取消)")


class TaskStepOutSchema(BaseSchema):
    """步骤明细响应模型（前端据此画流水线时间轴）"""

    task_id: int = Field(default=0, description="所属任务ID")
    doc_id: int = Field(default=0, description="关联文档ID")
    space_id: int = Field(default=0, description="所属空间ID")
    stage: int = Field(default=0, description="节点(1:解析 2:分片 3:向量化 4:写入pgvector)")
    stage_label: str = Field(default="", description="节点文案")
    seq: int = Field(default=0, description="节点序号")
    status: int = Field(default=int(TaskStatus.PENDING), description="状态(0:待执行 1:执行中 2:已完成 3:失败 4:取消)")
    input_count: int = Field(default=0, description="输入条数")
    output_count: int = Field(default=0, description="产出条数")
    elapsed_ms: int = Field(default=0, description="节点耗时(毫秒)")
    detail: dict | None = Field(default=None, description="节点摘要(来源/模式/向量维度等)")
    error_msg: str = Field(default="", description="失败原因")
    started_time: DateTimeStr | None = Field(default=None, description="开始时间")
    finished_time: DateTimeStr | None = Field(default=None, description="结束时间")

    @field_validator("detail", mode="before")
    @classmethod
    def _parse_detail(cls, value):
        """库里 detail 是 JSON 字符串，对外统一回 dict（空串/非法内容按 None）。"""
        if isinstance(value, dict) or value is None:
            return value
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None

    @model_validator(mode="after")
    def _fill_stage_label(self):
        """节点文案由 stage 推导，前端无需再维护一份映射。"""
        if not self.stage_label:
            self.stage_label = STAGE_LABELS.get(self.stage, "")
        return self


class TaskOutSchema(BaseSchema):
    """任务响应模型（前端据此轮询解析进度）"""

    task_type: int = Field(default=int(TaskType.PARSE), description="任务类型(1:文档解析)")
    space_id: int = Field(default=0, description="所属空间ID")
    doc_id: int = Field(default=0, description="关联文档ID")
    status: int = Field(default=int(TaskStatus.PENDING), description="状态(0:待执行 1:执行中 2:成功 3:失败 4:取消)")
    progress: int = Field(default=0, description="进度(0-100)")
    result: str = Field(default="", description="执行结果摘要")
    error_msg: str = Field(default="", description="失败原因")
    retry_count: int = Field(default=0, description="已重试次数(人工触发)")
    started_time: DateTimeStr | None = Field(default=None, description="开始时间")
    finished_time: DateTimeStr | None = Field(default=None, description="结束时间")


class TaskDetailOutSchema(TaskOutSchema):
    """任务详情（台账 + 四段节点明细，按 seq 升序）"""

    steps: list[TaskStepOutSchema] = Field(default_factory=list, description="节点明细")


class TaskQueryParam(BaseQueryParam):
    """任务查询参数"""

    space_id: int | None = Field(None, ge=1, description="所属空间ID", json_schema_extra={"q": "eq"})
    doc_id: int | None = Field(None, ge=1, description="关联文档ID", json_schema_extra={"q": "eq"})
    task_type: int | None = Field(None, ge=1, description="任务类型", json_schema_extra={"q": "eq"})
    status: int | None = Field(None, ge=0, le=4, description="任务状态", json_schema_extra={"q": "eq"})
