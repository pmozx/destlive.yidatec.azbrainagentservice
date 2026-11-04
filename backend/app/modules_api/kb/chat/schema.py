"""`/v1` 会话与生成接口 Schema（#08–#15）：与手册 §六「会话管理」「对话生成」逐字段对齐。

四处刻意的口径：

1. **`authorization` 块按接口区分必填**：#09 / #10 / #14 / #15 手册写「必填」，
   #08 / #11 / #12 / #13 写「可选」。不能一律放宽成可选——缺块本身就是可比对的契约差异；
   块的时效（跨度 ≤120s、未过期）由路由依赖 ``enforced_authorization`` 统一校验。
2. **枚举值只在这一层出现**：``operation`` / ``status`` / ``finish_reason`` 是对外口径，
   与库内状态（``running`` / ``succeeded``）不是同一套，映射集中在 ``service.py``，
   避免库内值直接漏进响应。
3. **时间统一 ``YYYY-MM-DDTHH:MM:SSZ``**：手册所有 ``created_at`` / ``updated_at`` 示例都是
   带 ``Z`` 的 UTC 秒级字符串；不做毫秒、不输出 ``+08:00``（同一个时刻换写法会让调用方
   按不同方式解析，比对必然对不上）。
4. **可选字段一律 ``None``**：``response_model_exclude_none=True`` 负责把「本场景不适用」
   的字段整体省略——手册对 ``content`` / ``citations`` / ``reason`` 的要求是「条件必返、
   否则禁止返回」，用空串占位等于凭空多给一个字段。
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.validator import ensure_non_blank

# 与领域层共用的取值：标题上限只在 chat_session.schema 定义一次
from app.modules.kb.chat_session.schema import MAX_TITLE_LENGTH

# ── 契约枚举 ────────────────────────────────────────────────────────────────
OPERATION_COMPLETION = "completion"
OPERATION_MUTATION = "mutation"
OPERATION_DELETE = "delete"

STATUS_PENDING = "pending"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"

COMPLETION_STATUS_COMPLETED = "completed"


class ChatBaseRequest(BaseModel):
    """#08–#15 公共正文：三个身份字段一律必填。

    ``tenant_id`` 只做回显与审计，不参与归属判断（本侧会话表不存租户）；
    归属只看 ``user_id``（GSI 登录用户的数字主键，字符串形式）。
    """

    model_config = ConfigDict(extra="ignore")

    request_id: str = Field(description="GSI 本次操作 ID；写操作同时是幂等键")
    tenant_id: str = Field(description="租户标识，当前固定 gsi-default")
    user_id: str = Field(description="GSI 登录用户的稳定数字主键（字符串）")

    @field_validator("request_id", "tenant_id", "user_id")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return ensure_non_blank(value)


class ChatAuthorization(BaseModel):
    """短期授权块（时效校验在路由依赖里，这里只声明必填与形状）。"""

    model_config = ConfigDict(extra="ignore")

    scope_id: str = Field(description="GSI 本次授权追踪标识，#14 会原样回显为 authorization_scope_id")
    issued_at: int = Field(ge=0, description="签发时间，Unix 毫秒")
    expires_at: int = Field(ge=0, description="到期时间，Unix 毫秒，跨度最长 120 秒")

    @field_validator("scope_id")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return ensure_non_blank(value)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# §一  请求正文（#08–#15）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class ChatSessionCreateRequest(ChatBaseRequest):
    """#08 创建会话。"""

    title: str | None = Field(default=None, max_length=MAX_TITLE_LENGTH, description="可选标题，缺省「新对话」")
    authorization: ChatAuthorization | None = Field(default=None, description="可选授权块（#08 手册标注可选）")

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, value: str | None) -> str | None:
        return ensure_non_blank(value) if value is not None else None


class ChatSessionQueryRequest(ChatBaseRequest):
    """#09 分页查询我的会话。"""

    authorization: ChatAuthorization
    cursor: str | None = Field(default=None, description="上一页的 next_cursor；首次不传")
    limit: int = Field(default=20, ge=1, le=100, description="每页条数，默认 20")

    @field_validator("cursor")
    @classmethod
    def _cursor_not_blank(cls, value: str | None) -> str | None:
        return ensure_non_blank(value) if value is not None else None


class ChatSessionDetailRequest(ChatBaseRequest):
    """#10 会话详情（可选返回历史分页）。"""

    authorization: ChatAuthorization
    include_messages: bool = Field(default=False, description="是否返回历史消息，默认 false")
    cursor: str | None = Field(default=None, description="历史消息游标")
    limit: int = Field(default=20, ge=1, le=100, description="历史每页条数，默认 20")

    @field_validator("cursor")
    @classmethod
    def _cursor_not_blank(cls, value: str | None) -> str | None:
        return ensure_non_blank(value) if value is not None else None


class ChatSessionRenameRequest(ChatBaseRequest):
    """#11 修改会话标题。"""

    title: str = Field(min_length=1, max_length=MAX_TITLE_LENGTH, description="新的非空标题")
    authorization: ChatAuthorization | None = Field(default=None, description="可选授权块")

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, value: str) -> str:
        return ensure_non_blank(value)


class ChatSessionDeleteRequest(ChatBaseRequest):
    """#12 删除会话（DELETE 带 JSON 正文，正文只用于身份与幂等）。"""

    authorization: ChatAuthorization | None = Field(default=None, description="可选授权块")


class ChatSessionClearRequest(ChatBaseRequest):
    """#13 清空会话历史（同上，正文只承载身份与幂等键）。"""

    authorization: ChatAuthorization | None = Field(default=None, description="可选授权块")


class ChatCompletionOptions(BaseModel):
    """#14 输出设置。"""

    model_config = ConfigDict(extra="ignore")

    language: str = Field(description="回答语言，如 zh-CN")
    max_output_tokens: int = Field(ge=1, le=8192, description="最大输出 token，1–8192")

    @field_validator("language")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return ensure_non_blank(value)


class ChatCompletionRequest(ChatBaseRequest):
    """#14 发送问题并生成回答（本期非流式）。"""

    session_id: str = Field(description="AI 分配的会话 ID；必须先 #08 创建，不能传空串")
    question: str = Field(description="本轮用户问题，非空")
    prompt_text: str = Field(description="GSI 服务端配置的提示词正文，不是 prompt_id")
    model_key: str = Field(description="约定的 Chat 模型标识，当前固定 default")
    stream: bool = Field(description="本期必须 false；true 回 UNSUPPORTED_STREAM")
    authorization: ChatAuthorization
    options: ChatCompletionOptions

    @field_validator("session_id", "question", "prompt_text", "model_key")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return ensure_non_blank(value)


class ChatRequestResultRequest(ChatBaseRequest):
    """#15 查询超时操作的结果：正文 ``request_id`` 是本次查询的新 ID。"""

    authorization: ChatAuthorization


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# §二  响应体
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class ChatSessionPayload(BaseModel):
    """#08 / #11 响应，也是 #15 「mutation」分支的 result。"""

    request_id: str
    session_id: str
    title: str
    created_at: str
    updated_at: str


class ChatSessionItem(BaseModel):
    """#09 会话列表项（无 ``request_id``：它在最外层）。"""

    session_id: str
    title: str
    created_at: str
    updated_at: str


class ChatSessionListResponse(BaseModel):
    """#09 响应。"""

    request_id: str
    sessions: list[ChatSessionItem]
    next_cursor: str | None = None


class ChatCitation(BaseModel):
    """引用八元组 + ``citation_id``：``quote`` 必须是已授权切片正文的逐字子串。"""

    index: int = Field(ge=1)
    citation_id: str
    resource_type: str
    resource_id: str
    publication_id: str
    record_id: str
    version_id: str
    revision: int = Field(ge=1)
    chunk_id: str
    quote: str


class ChatSourceSnapshot(BaseModel):
    """来源依赖六元组：覆盖进入上下文的**全部**来源，含模型未显式引用的那些。"""

    resource_type: str
    resource_id: str
    publication_id: str
    record_id: str
    version_id: str
    revision: int = Field(ge=1)


class ChatHistoryMessage(BaseModel):
    """#10 历史消息：不可展示时只回 ID / 角色 / 时间 / 原因。"""

    message_id: str
    role: str = Field(description="user / assistant")
    content_visible: bool
    created_at: str
    content: str | None = None
    citations: list[ChatCitation] | None = None
    source_snapshots: list[ChatSourceSnapshot] | None = None
    no_evidence: bool | None = None
    reason: str | None = None


class ChatSessionDetailResponse(BaseModel):
    """#10 响应；``include_messages=false`` 时 ``messages`` / ``next_cursor`` 整体省略。"""

    request_id: str
    session_id: str
    title: str
    created_at: str
    updated_at: str
    messages: list[ChatHistoryMessage] | None = None
    next_cursor: str | None = None


class ChatSessionDeleteReceipt(BaseModel):
    """#12 响应。"""

    request_id: str
    session_id: str
    deleted: bool


class ChatSessionClearReceipt(BaseModel):
    """#13 响应。"""

    request_id: str
    session_id: str
    cleared: bool


class ChatCompletionResponse(BaseModel):
    """#14 响应，也是 #15 「completion」分支的 result（由台账补回 ``answer``）。"""

    request_id: str
    session_id: str
    user_message_id: str
    assistant_message_id: str
    status: str = Field(default=COMPLETION_STATUS_COMPLETED, description="同步成功固定 completed")
    answer: str
    citations: list[ChatCitation]
    source_snapshots: list[ChatSourceSnapshot]
    authorization_scope_id: str
    no_evidence: bool
    finish_reason: str = Field(description="stop / no_evidence")


class ChatResultError(BaseModel):
    """#15 失败分支的错误体（与原请求失败时的错误码同源）。"""

    code: str
    message: str
    retryable: bool


class ChatRequestResultResponse(BaseModel):
    """#15 响应：``result`` 的形状由 ``operation`` 决定，故按 ``dict`` 透出。"""

    request_id: str
    operation: str = Field(description="completion / mutation / delete")
    status: str = Field(description="pending / completed / failed / cancelled")
    result_visible: bool | None = None
    result: dict[str, Any] | None = None
    reason: str | None = None
    error: ChatResultError | None = None
