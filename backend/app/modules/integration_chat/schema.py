"""集成-AI 问答-对外契约（会话 / 消息 / 流式入参 / 引用快照）。

引用快照与 SSE ``sources`` 帧共用 ``CiteItemSchema``，线上键名统一 camelCase，
前端引用卡片直接消费该结构（与 fastapi_admin 的问答前端保持一致）。
"""

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.base_schema import BaseQueryParam, BaseSchema
from app.core.validator import DateTimeStr


class CiteItemSchema(BaseModel):
    """引用快照项（消息 cites 元素 / SSE sources 帧元素，键名 camelCase）"""

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    doc_id: int = Field(..., alias="docId", ge=1, description="文档ID")
    chunk_id: str = Field(..., alias="chunkId", min_length=1, description="分片ID（PGVector 主键）")
    doc: str = Field(..., description="文档标题")
    page: int | None = Field(default=None, ge=1, description="页码")
    snippet: str = Field(..., description="命中片段")
    space: str = Field(..., description="空间名称")
    cat: str = Field(..., description="分类名称")


class SessionCreateSchema(BaseModel):
    """问答会话创建模型"""

    title: str | None = Field(default=None, max_length=128, description="会话标题（默认取首问前20字）")
    scope_type: int = Field(default=1, ge=1, le=2, description="问答范围(1:全部空间 2:指定空间)")
    space_ids: list[int] | None = Field(default=None, description="指定空间ID数组（scope_type=2 时必填）")

    @model_validator(mode="after")
    def _validate_scope(self):
        if self.scope_type == 2 and not self.space_ids:
            raise ValueError("指定空间问答时 space_ids 不能为空")
        return self


class SessionUpdateSchema(BaseModel):
    """问答会话重命名模型"""

    title: str = Field(..., min_length=1, max_length=128, description="会话标题")


class SessionOutSchema(BaseSchema):
    """问答会话响应模型"""

    model_config = ConfigDict(from_attributes=True)

    # 表里没有 session_id 列：由服务层按主键回填（见 session/service.py::_to_out），
    # 因此这里必须给默认值，否则 model_validate(ORM 对象) 会因缺字段直接报错
    session_id: str = Field(default="", description="会话ID（字符串形式，兼容前端既有封装）")
    title: str | None = Field(default=None, description="会话标题")
    scope_type: int = Field(default=1, description="问答范围(1:全部空间 2:指定空间)")
    space_ids: list[int] | None = Field(default=None, description="指定空间ID数组")
    message_count: int = Field(default=0, ge=0, description="消息数")
    last_message_time: DateTimeStr | None = Field(default=None, description="最近消息时间")


class MessageOutSchema(BaseSchema):
    """问答消息响应模型"""

    model_config = ConfigDict(from_attributes=True)

    session_id: int = Field(..., ge=1, description="所属会话ID")
    role: str = Field(..., description="角色(user/assistant)")
    content: str = Field(..., description="正文（assistant 为 Markdown）")
    analysis: dict | None = Field(default=None, description="思考链路（仅 assistant）")
    cites: list[CiteItemSchema] | None = Field(default=None, description="引用快照数组")
    follow_ups: list[str] | None = Field(default=None, description="问题拓展数组")
    disclaimer: str | None = Field(default=None, description="免责声明文案")
    feedback: int | None = Field(default=None, ge=-1, le=1, description="答案反馈(1:有用 -1:无用)")
    not_found: bool = Field(default=False, description="拒答标记")
    model_name: str | None = Field(default=None, description="生成模型")
    tokens_in: int = Field(default=0, ge=0, description="输入 Token")
    tokens_out: int = Field(default=0, ge=0, description="输出 Token")
    latency_ms: int = Field(default=0, ge=0, description="端到端耗时(毫秒)")


class SessionDetailOutSchema(BaseModel):
    """会话详情（会话信息 + 消息明细，时间升序）"""

    session: SessionOutSchema = Field(..., description="会话信息")
    messages: list[MessageOutSchema] = Field(default_factory=list, description="消息明细")


class SessionQueryParam(BaseQueryParam):
    """问答会话查询参数"""

    title: str | None = Field(None, description="会话标题", json_schema_extra={"q": "like"})
    scope_type: int | None = Field(None, ge=1, le=2, description="问答范围", json_schema_extra={"q": "eq"})


class ChatStreamInSchema(SessionCreateSchema):
    """流式问答请求模型（POST /integration-chat/stream，SSE）"""

    question: str = Field(..., min_length=1, max_length=2000, description="用户问题")
    session_id: int | None = Field(default=None, ge=1, description="会话ID（为空视为新会话）")


class FeedbackInSchema(BaseModel):
    """消息答案反馈模型"""

    feedback: int = Field(..., description="反馈值(1:有用 -1:无用)")

    @field_validator("feedback")
    @classmethod
    def _validate_feedback(cls, value: int) -> int:
        if value not in {1, -1}:
            raise ValueError("反馈值仅支持 1(有用) 或 -1(无用)")
        return value
