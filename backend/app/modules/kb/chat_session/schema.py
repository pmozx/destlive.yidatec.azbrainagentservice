"""问答会话 Schema：领域层数据结构 + 管理端 HTTP 契约。

本表没有 ``tenant_id`` 列：契约的 ``tenant_id`` 由 GSI 固定为 ``gsi-default`` 且不参与
归属判断，归属只按签名身份里的 ``user_id``（``sbp_sys_user.id``）校验，因此领域层
也不需要携带租户字段——多写一个不入库的字段只会让「以为按租户隔离了」的错觉出现。

会话状态（库内取值）：
- ``active``：正常会话，首轮提问后由 ``last_active_at`` 表示活跃时间；
- ``cleared``：清空过历史，会话壳仍在，**迟到**的生成结果必须按此丢弃；
- ``deleted``：已删除（软删），列表/详情一律按 404 隐藏，且不允许被迟到写入复活。
"""

from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.core.base_schema import BaseQueryParam, BaseSchema
from app.core.validator import DateTimeStr, ensure_non_blank

# ── 常量 ────────────────────────────────────────────────────────────────────
#: 契约默认标题：``#08`` 不传 title 时使用，且首轮提问后仅在仍为该值时改写
DEFAULT_SESSION_TITLE = "新对话"
#: 契约上限：标题最多 100 字符（模型列宽 200 是为后续扩展留余量）
MAX_TITLE_LENGTH = 100
#: 首轮提问自动派生标题的截断长度（与契约上限一致，避免超长问题撑爆标题）
DERIVED_TITLE_LENGTH = MAX_TITLE_LENGTH

SESSION_ACTIVE = "active"
SESSION_CLEARED = "cleared"
SESSION_DELETED = "deleted"
SESSION_STATUSES: tuple[str, ...] = (SESSION_ACTIVE, SESSION_CLEARED, SESSION_DELETED)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# §一  领域层 dataclass（契约 #08–#13 的服务层入参/回执）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@dataclass(frozen=True)
class SessionRecord:
    """一个会话的领域快照（``session_id`` 即表里的 ``uuid``，由 AI 分配）。"""

    session_id: str
    user_id: int
    title: str
    message_count: int
    status: str
    created_time: datetime
    updated_time: datetime
    last_active_at: datetime | None = None


@dataclass(frozen=True)
class SessionPage:
    """会话列表一页：``next_cursor`` 为 None 表示最后一页。"""

    items: tuple[SessionRecord, ...]
    next_cursor: str | None = None


def derive_title(question: str) -> str:
    """首轮提问 → 会话标题：折叠空白后截断，空问题退回默认标题。"""
    compact = " ".join((question or "").split())
    if not compact:
        return DEFAULT_SESSION_TITLE
    return compact[:DERIVED_TITLE_LENGTH]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# §二  管理端 Pydantic Schema（/kb/chat-session）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class KbChatSessionOutSchema(BaseSchema):
    """会话列表/详情响应（``session_id`` 同时回显，避免前端从 uuid 字段猜语义）。"""

    session_id: str = Field(default="", description="会话 ID（即表 uuid，AI 分配）")
    user_id: int = Field(..., description="归属用户主键（sbp_sys_user.id）")
    title: str = Field(default=DEFAULT_SESSION_TITLE, description="会话标题")
    message_count: int = Field(default=0, description="消息数")
    last_active_at: DateTimeStr | None = Field(default=None, description="最近活跃时间")
    status: str = Field(default=SESSION_ACTIVE, description="active/cleared/deleted")


class KbChatSessionDetailOutSchema(KbChatSessionOutSchema):
    """会话详情响应（补充消息总数，消息正文走 /kb/chat-message/list 分页取）。"""

    message_total: int = Field(default=0, description="未删除消息总数")


class KbChatSessionQueryParam(BaseQueryParam):
    """会话查询参数（``q`` 标记决定 CRUD 层的比较方式）。"""

    session_id: str | None = Field(None, description="会话 ID", json_schema_extra={"q": "like"})
    user_id: int | None = Field(None, description="归属用户主键", json_schema_extra={"q": "eq"})
    title: str | None = Field(None, description="会话标题", json_schema_extra={"q": "like"})
    status: str | None = Field(None, description="会话状态", json_schema_extra={"q": "eq"})


class KbChatSessionUpdateSchema(BaseModel):
    """改标题：只改标题，不动消息与状态。"""

    title: str = Field(..., min_length=1, max_length=MAX_TITLE_LENGTH, description="新的非空标题")

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        return ensure_non_blank(value)
