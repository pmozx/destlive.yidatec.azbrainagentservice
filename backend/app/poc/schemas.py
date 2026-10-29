"""`/v1` 请求模型（POC 版）：字段名、必填性、枚举值逐条对齐 v2.1 手册。

约定：
1. 只定义**请求**模型。响应体由各业务函数按契约手工拼装（`parser.py` / `store.py` / `chat.py`），
   不用 `response_model` 二次校验 —— 契约里大量字段是「可省略或 null」，模型往返反而容易把
   可选键序列化成 null，与手册示例不一致；
2. 字符串统一 `strip_whitespace + min_length=1`：手册多处要求「不能为 null、空字符串或纯空白」，
   校验失败由 `PocApiRoute` 统一转成 400 `invalid_request`；
3. `metadata` 允许额外键（`extra="allow"`）：手册明说「新增 metadata 涵盖新增页面字段」，
   POC 不认识的新字段应按上下文透传而不是拒收（严格白名单留到 T2.3 再定）。
"""

from hashlib import sha256
from json import dumps
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from .errors import UNSUPPORTED_STREAM, PocApiError

NonBlankStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
ResourceType = Literal["document", "faq"]


class _RequestModel(BaseModel):
    """请求模型基类：忽略未知字段（GSI 灰度上线时不至于整单 400）。"""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class AuthorizationSchema(_RequestModel):
    """短期授权块：AI 用它确认「本次调用是一次新授权」，真正的权限要靠共享表实时计算（T4.3）。"""

    scope_id: NonBlankStr
    issued_at: int = Field(description="签发时间，Unix 毫秒")
    expires_at: int = Field(description="到期时间，Unix 毫秒，最长 120 秒")


class MetadataSchema(_RequestModel):
    """知识元数据快照（仅作解析/生成上下文，**不据此授权**）。"""

    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)

    title: str | None = None
    doc_type: Literal["document", "faq", "code", "api", "test", "runbook", "handover"] | None = None
    owner: str | None = None
    author: str | None = None
    team: str | None = None
    language: str | None = None
    trust: Literal["a", "b", "c"] | None = None
    summary: str | None = None
    content: str | None = None
    tags: list[str] | None = None
    expire_at: str | None = None
    scheduled_publish_at: str | None = None
    invisible_teams: list[str] | None = None
    audience: dict[str, list[str]] | None = None
    cover_url: str | None = None
    cover_name: str | None = None
    linked_doc_id: str | None = None
    linked_section: str | None = None
    change_impact_level: Literal["minor", "substantial"] | None = None
    units: list[dict] | None = None


# ── #01 提交解析任务 ────────────────────────────────────────────────────


class ParseOptionsSchema(_RequestModel):
    chunk: bool
    language: NonBlankStr


class ParseJobRequest(_RequestModel):
    job_id: NonBlankStr
    doc_id: NonBlankStr
    version_id: NonBlankStr
    file_url: NonBlankStr
    file_name: NonBlankStr
    prompt_text: str | None = None
    model_key: str | None = None
    options: ParseOptionsSchema
    tenant_id: NonBlankStr
    resource_type: ResourceType
    file_id: str | None = None
    file_size: int | None = Field(default=None, ge=0)
    metadata: MetadataSchema

    @model_validator(mode="after")
    def _require_faq_prompt(self) -> "ParseJobRequest":
        """FAQ 解析必须带提示词与模型键（手册明示 400 invalid_request）。"""
        if self.resource_type == "faq":
            missing = [
                name
                for name, value in (("prompt_text", self.prompt_text), ("model_key", self.model_key))
                if not (value or "").strip()
            ]
            if missing:
                raise ValueError(f"resource_type 为 faq 时，{' 与 '.join(missing)} 必填且不能仅包含空白字符。")
        return self


# ── #03 首次发布写入正式切片 ──────────────────────────────────────────


class KnowledgeEntryChunkSchema(_RequestModel):
    chunk_id: NonBlankStr
    seq: int = Field(ge=1)
    heading: str | None = None
    content: NonBlankStr
    page_from: int | None = Field(default=None, ge=1)
    page_to: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _check_page_range(self) -> "KnowledgeEntryChunkSchema":
        if self.page_from and self.page_to and self.page_to < self.page_from:
            raise ValueError("page_to 不得小于 page_from。")
        return self


class KnowledgeEntryRequest(_RequestModel):
    """首发布写入：`publication_id` 已存在必须 409，不能隐式覆盖。"""

    request_id: NonBlankStr
    tenant_id: NonBlankStr
    user_id: NonBlankStr
    resource_type: ResourceType
    resource_id: NonBlankStr
    publication_id: NonBlankStr
    record_id: NonBlankStr
    version_id: NonBlankStr
    revision: int = Field(ge=1)
    knowledge_no: NonBlankStr
    metadata: MetadataSchema
    content_hash: NonBlankStr
    chunks: list[KnowledgeEntryChunkSchema]


# ── #07 检索 ──────────────────────────────────────────────────────────


class KnowledgeSearchRequest(_RequestModel):
    request_id: NonBlankStr
    query: NonBlankStr
    mode: Literal["keyword", "natural_language"]
    limit: int = Field(ge=1, le=100)
    tenant_id: NonBlankStr
    user_id: NonBlankStr
    authorization: AuthorizationSchema


# ── #14 生成回答 ──────────────────────────────────────────────────────


class ChatOutputOptions(_RequestModel):
    language: NonBlankStr
    max_output_tokens: int = Field(ge=1, le=8192)


class ChatCompletionRequest(_RequestModel):
    """本期只支持非流式；`stream=true` 立刻回 400 `unsupported_stream`。

    `session_id` 手册标为必填且「ai 分配的会话 id」，但首轮对话 GSI 手里没有值 ——
    POC 允许传空串并由 AI 生成 `ai-session-*`，疑点见 T0.4 清单。
    """

    request_id: NonBlankStr
    tenant_id: NonBlankStr
    user_id: NonBlankStr
    session_id: str = ""
    question: NonBlankStr
    prompt_text: NonBlankStr
    model_key: NonBlankStr
    stream: bool = False
    authorization: AuthorizationSchema
    options: ChatOutputOptions

    @model_validator(mode="after")
    def _reject_stream(self) -> "ChatCompletionRequest":
        if self.stream:
            raise PocApiError(UNSUPPORTED_STREAM, "本期仅支持非流式回答，请将 stream 置为 false。", status_code=400)
        return self


def request_digest(payload: BaseModel) -> str:
    """请求正文摘要（幂等比对用）：键排序 + 紧凑分隔符，同一模型两次得到同一摘要。

    注意区别于签名用的 `body_digest`：那个算的是 GSI 发来的**原始字节**；这里算的是
    校验后的规范化 JSON，用来判断「同 ID 的两次请求正文是否真的一样」。
    """
    canonical = dumps(payload.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return sha256(canonical.encode("utf-8")).hexdigest()


__all__ = [
    "AuthorizationSchema",
    "ChatCompletionRequest",
    "ChatOutputOptions",
    "KnowledgeEntryChunkSchema",
    "KnowledgeEntryRequest",
    "KnowledgeSearchRequest",
    "MetadataSchema",
    "ParseJobRequest",
    "ParseOptionsSchema",
    "request_digest",
]
