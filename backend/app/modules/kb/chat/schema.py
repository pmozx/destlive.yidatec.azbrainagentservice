"""问答编排数据结构：证据、生成结果、历史投影 + 管理端 AI 对话入参。

三个字段口径的由来：

- ``quote`` **必须逐字**（契约：能在对应切片的授权正文里找到）。所以不存摘要、不折叠空白，
  只做首尾裁剪与长度截断——截断后的子串仍然逐字可查；引用因此按命中键回查库内原文
  （``chat/crud.py``），而不是直接拿检索层回传的 ``snippet``。
- ``content`` 是**完整切片正文**，只进模型上下文、不进响应。两个字段必须分开：引用回放
  要短且可逐字校验，而给模型的依据如果也只有 160 字，``KB_CONTEXT_MAX_CHARS`` 就永远用不上，
  模型只能拿半截切片硬答。
- ``citations`` 是 **八元组**，``source_snapshots`` 是 **六元组**：前者多 ``chunk_id`` 与
  ``quote``。两者同时返回是契约要求：``source_snapshots`` 要覆盖「进入上下文的全部来源」，
  包括模型没显式引用的那些，GSI 据此逐项再授权。
- ``knowledge_deps`` 落库只留 ``publication_id + record_id + revision``：判定失效只需要这三项
  （当前指针是否还等于当时的 ``record_id``、发布单元是否仍生效、是否仍在授权范围内），
  存更多字段只会让失效判定多几个可能不一致的口径。
"""

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.validator import ensure_non_blank

from ..chat_message.schema import MessageRecord

# ── 契约枚举 ────────────────────────────────────────────────────────────────
FINISH_STOP = "stop"
FINISH_NO_EVIDENCE = "no_evidence"
#: 内容不可展示的唯一原因（契约 ``reason`` 只允许这一个值）
REASON_SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"

#: 证据不足时的固定拒答话术：契约要求「证据不足时明确拒答」，不能拿模型常识编。
#: 不由模型生成，避免同一条件下时好时坏。
NO_EVIDENCE_ANSWER = (
    "抱歉，在您有权访问的知识库中没有找到足以回答这个问题的依据。"
    "建议补充更具体的信息，或联系相关业务同事确认。"
)


@dataclass(frozen=True)
class ChatEvidence:
    """一条进入本轮上下文的证据（切片级），``index`` 即正文里的 ``[n]``。

    ``content`` 给模型读，``quote`` 给引用回放；两者同源但长度口径不同（见模块说明）。
    """

    index: int
    resource_type: str
    resource_id: str
    publication_id: str
    record_id: str
    version_id: str
    revision: int
    chunk_id: str
    quote: str
    content: str = ""
    title: str = ""
    score: float = 0.0

    @property
    def citation_id(self) -> str:
        """引用别名：本轮内唯一，从 1 连续递增（与 ``index`` 同源，不另起编号体系）。"""
        return f"c{self.index}"

    def to_citation(self) -> dict[str, Any]:
        """→ 契约 ``citations[]`` 的八元组（顺序与手册字段表一致）。"""
        return {
            "index": self.index,
            "citation_id": self.citation_id,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "publication_id": self.publication_id,
            "record_id": self.record_id,
            "version_id": self.version_id,
            "revision": self.revision,
            "chunk_id": self.chunk_id,
            "quote": self.quote,
        }

    def to_snapshot(self) -> dict[str, Any]:
        """→ 契约 ``source_snapshots[]`` 的六元组（不含 chunk 与 quote）。"""
        return {
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "publication_id": self.publication_id,
            "record_id": self.record_id,
            "version_id": self.version_id,
            "revision": self.revision,
        }

    def to_dependency(self) -> dict[str, Any]:
        """→ 落库的 ``knowledge_deps`` 一项（失效判定只需这三项）。"""
        return {
            "publication_id": self.publication_id,
            "record_id": self.record_id,
            "revision": self.revision,
        }


@dataclass(frozen=True)
class ChatTurn:
    """一轮问答的生成结果（#14 响应与 #15 结果结构 1 同源）。"""

    session_id: str
    user_message_id: str
    assistant_message_id: str
    answer: str
    citations: tuple[dict[str, Any], ...]
    source_snapshots: tuple[dict[str, Any], ...]
    no_evidence: bool
    finish_reason: str


@dataclass(frozen=True)
class HistoryMessage:
    """#10 的一条历史消息：不可展示时只回 ID、角色、时间与原因。"""

    record: MessageRecord
    content_visible: bool
    reason: str | None = None


@dataclass(frozen=True)
class HistoryPage:
    """一页历史：``next_cursor`` 为 None 表示最后一页。"""

    items: tuple[HistoryMessage, ...]
    next_cursor: str | None = None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# §三  管理端 Pydantic Schema（/kb/chat/ask：AI 对话试问台）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

#: 管理端 AI 对话页的默认提示词：与契约 ``prompt_text`` 同职（只写业务边界），
#: 引用格式与「没证据就拒答」的约束由 ``prompt.py`` 统一追加；页面留空时用它。
ADMIN_DEFAULT_PROMPT_TEXT = "你是企业知识库问答助手，只能依据【参考资料】回答问题，资料未覆盖时直接说明。"


class KbChatAskSchema(BaseModel):
    """AI 对话入参：会话可留空（首轮自动建），``user_id`` 必填（授权与会话归属都靠它）。

    与契约 #14 的差别只有两处：没有 ``request_id`` / ``authorization``（管理端走登录态，
    页面是人在点，没有重试语义），以及提示词可留空由后端补默认值。
    """

    session_id: str | None = Field(default=None, max_length=64, description="会话 ID；留空则自动新建会话")
    user_id: str = Field(
        ..., min_length=1, max_length=64, description="用户主键（数字字符串），决定授权范围与会话归属"
    )
    question: str = Field(..., min_length=1, max_length=500, description="本轮问题")
    tenant_id: str = Field(default="gsi-default", min_length=1, max_length=64, description="租户 ID")
    prompt_text: str | None = Field(default=None, max_length=4000, description="系统提示词；留空用默认提示词")
    language: str = Field(default="zh-CN", max_length=16, description="回答语言")
    max_output_tokens: int | None = Field(default=None, ge=1, le=8192, description="回答 token 上限；留空用配置默认")

    @field_validator("user_id", "question", "tenant_id")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        return ensure_non_blank(value)


class KbChatCitationOutSchema(BaseModel):
    """管理端引用投影：与契约引用八元组同构，页面直接照着渲染 ``[index]`` 与原文。"""

    model_config = ConfigDict(from_attributes=True)

    index: int = Field(..., ge=1, description="本轮内编号，与正文里的 [n] 一致")
    citation_id: str = Field(..., description="引用别名（c1、c2…）")
    resource_type: str = Field(..., description="资源类型")
    resource_id: str = Field(..., description="原始资源 ID")
    publication_id: str = Field(..., description="发布单元 ID")
    record_id: str = Field(..., description="版本快照记录 ID")
    version_id: str = Field(..., description="版本 ID")
    revision: int = Field(..., description="版本号")
    chunk_id: str = Field(..., description="切片 ID")
    quote: str = Field(..., description="引用原文（可在切片正文里逐字校验）")


class KbChatAskOutSchema(BaseModel):
    """一轮问答的结果：页面据此渲染回答、引用与「证据不足」提示。"""

    session_id: str = Field(..., description="本轮落库的会话 ID（自动建的会话也在此返回）")
    user_message_id: str = Field(..., description="用户消息 ID")
    assistant_message_id: str = Field(..., description="助手消息 ID")
    answer: str = Field(..., description="回答正文")
    no_evidence: bool = Field(..., description="是否因证据不足而拒答")
    finish_reason: str = Field(..., description="stop / no_evidence")
    elapsed_ms: int = Field(..., ge=0, description="检索 + 生成的整条链路耗时（毫秒）")
    citations: list[KbChatCitationOutSchema] = Field(default_factory=list, description="引用列表")
