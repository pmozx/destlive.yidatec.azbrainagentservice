"""解析任务的入库 / 更新 Schema（CRUD 的类型入口，不做契约字段校验）。"""

from typing import Any, Literal

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
    artifacts: dict[str, Any] | None = Field(
        default=None, description="MinerU 产物在对象存储的键（markdown/content_list/images）"
    )
    content_hash: str = Field(..., description="产物整体哈希")
    publication_id: str | None = Field(default=None, description="已写入的发布单元 ID（未写入为 null）")
    published_time: DateTimeStr | None = Field(default=None, description="最近一次写入知识平台的时间")
    created_time: DateTimeStr | None = Field(default=None, description="产物写入时间")


class KbParseStepOutSchema(BaseModel):
    """一步的执行记录：页面用它渲染步骤时间线与失败原因（不带续跑快照）。"""

    model_config = {"from_attributes": True}

    id: int = Field(..., description="记录行 ID")
    attempt: int = Field(..., description="第几次执行（1 起）")
    step: str = Field(..., description="extract/artifacts/faq/chunk/pages/suggest/persist")
    status: str = Field(..., description="running/succeeded/failed/skipped")
    fatal: bool = Field(..., description="失败是否中断任务（false=降级继续）")
    started_time: DateTimeStr = Field(..., description="开始时间")
    finished_time: DateTimeStr | None = Field(default=None, description="结束时间（running 为 null）")
    duration_ms: int | None = Field(default=None, description="耗时（毫秒）")
    error_code: str | None = Field(default=None, description="步骤失败码")
    error_message: str | None = Field(default=None, description="步骤失败原因 / 跳过原因")
    detail: dict[str, Any] = Field(default_factory=dict, description="轻量元数据（解析器/页数/条数）")


class KbParseRetrySchema(BaseModel):
    """重跑请求：缺省从上一轮失败的那一步继续。"""

    from_step: Literal["extract", "artifacts", "faq", "chunk", "pages", "suggest", "persist"] | None = Field(
        default=None,
        description="从哪一步重跑；缺省取上一轮第一处失败的步骤，再往前自动回退到有快照的位置",
    )


class KbParseChunkUpdateSchema(BaseModel):
    """编辑解析产物里的单条切片：只改该条正文/标题，不重算整篇产物哈希。"""

    seq: int = Field(..., ge=1, description="切片序号（产物内唯一）")
    content: str = Field(..., min_length=1, max_length=20000, description="切片正文（Markdown 源文本）")
    heading: str | None = Field(default=None, max_length=512, description="切片标题；缺省表示不动")


class KbParseChunkOutSchema(BaseModel):
    """切片编辑回执：前端据此局部刷新，不必整份重拉详情。"""

    seq: int = Field(..., description="切片序号")
    heading: str | None = Field(default=None, description="切片标题（None 表示产物里没有这个键）")
    content: str = Field(..., description="切片正文")
    edited: bool = Field(default=True, description="是否为人工编辑过的切片")
    edited_time: str | None = Field(default=None, description="最近一次编辑时间（ISO 8601）")


class KbParsePublishSchema(BaseModel):
    """把解析产物写成一份正式知识（03.1 等价）：切片取自产物，不再二次切分。

    页面上的产物已逐条审过（可能人工修订），因此不接新的正文；调用方只需补
    「写成哪个发布单元」的展示字段，缺省值由服务端从任务快照推导。
    """

    publication_id: str | None = Field(
        default=None, min_length=1, max_length=64, description="发布单元 ID，省略自动生成"
    )
    title: str | None = Field(default=None, max_length=200, description="标题，省略取原文件名（去扩展名）")
    resource_type: Literal["document", "faq"] | None = Field(
        default=None, description="知识类型，省略取任务上的类型"
    )
    tenant_id: str | None = Field(
        default=None, max_length=64, description="租户 ID，省略取任务快照，再缺省用契约固定租户"
    )
    resource_id: str | None = Field(default=None, max_length=64, description="原始资源 ID，省略取任务快照 doc_id")
    category: str | None = Field(default=None, max_length=64, description="知识分类")
    summary: str | None = Field(default=None, description="内容摘要")
    tags: list[str] = Field(default_factory=list, description="标签")
    owner: str | None = Field(default=None, max_length=64, description="维护人")
    knowledge_no: str | None = Field(default=None, max_length=64, description="知识编号，省略取发布单元 ID")


class KbParsePublishOutSchema(BaseModel):
    """写入回执：发布单元与落库版本，页面据此显示「已写入」与跳转公示。"""

    job_id: str = Field(..., description="parse_job.uuid")
    publication_id: str = Field(..., description="发布单元 ID")
    record_id: str = Field(..., description="新快照 ID")
    version_id: str = Field(..., description="契约版本 ID")
    revision: int = Field(..., description="新修订号")
    content_hash: str = Field(..., description="整包内容摘要（sha256:<hex>）")
    chunk_count: int = Field(..., description="本次写入的切片数")
    searchable: bool = Field(..., description="写入后是否可检索")
    status: str = Field(..., description="写入后的发布单元状态")
    published_time: DateTimeStr = Field(..., description="写入时间")


class KbParseJobOutSchema(BaseSchema):
    """解析任务列表响应（不返回原始 file_meta，避免大字段进列表）。"""

    uuid: str = Field(..., description="契约 job_id")
    resource_type: str = Field(..., description="document/faq")
    status: str = Field(..., description="queued/running/succeeded/failed")
    progress: int = Field(default=0, description="进度 0–100")
    attempt: int = Field(default=1, description="执行轮次（重跑会 +1）")
    error_code: str | None = Field(default=None, description="失败码")
    error_message: str | None = Field(default=None, description="失败说明")


class KbParseJobDetailOutSchema(KbParseJobOutSchema):
    """解析任务详情响应（含原始请求快照、解析产物与步骤台账）。"""

    file_meta: dict[str, Any] = Field(default_factory=dict, description="原始请求快照")
    result: KbParseResultOutSchema | None = Field(default=None, description="解析产物（未完成时为 null）")
    steps: list[KbParseStepOutSchema] = Field(default_factory=list, description="步骤台账（含历史执行轮次）")
    artifact_urls: dict[str, Any] | None = Field(
        default=None, description="MinerU 产物的预签名下载地址（对象存储未启用时为 null）"
    )


class KbParseJobQueryParam(BaseQueryParam):
    """解析任务查询参数。"""

    uuid: str | None = Field(None, description="任务 ID", json_schema_extra={"q": "like"})
    resource_type: str | None = Field(None, description="资源类型", json_schema_extra={"q": "eq"})
    status: str | None = Field(None, description="任务状态", json_schema_extra={"q": "eq"})
    error_code: str | None = Field(None, description="失败码", json_schema_extra={"q": "eq"})

