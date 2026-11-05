"""会话消息 Schema：领域层数据结构 + 管理端 HTTP 契约。

消息状态（库内取值）：

- ``active``：正常参与上下文与展示；
- ``hidden``：保留给「人工判定不可用」的整条下架（当前链路不用，依赖失效是**读时**判定，
  不落库——失效发生在写入之后，写死状态会让后来恢复的发布单元永远看不到历史回答）；
- ``deleted``：清空历史时批量置位，正文不再展示，但行保留：#15 的超时查询可能在清空
  之后才到，没有行就无法回答「这次请求当时是否成功」，也无法把已清空的结果正确藏起来。

``citations`` / ``knowledge_deps`` 用 ``list[dict]`` 而不是再定义一层 dataclass：它们的
真实形状由契约（引用八元组、依赖三元组）决定，领域层只做搬运，不在中间再映射一遍。
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import Field

from app.core.base_schema import BaseQueryParam, BaseSchema

# ── 常量 ────────────────────────────────────────────────────────────────────
MESSAGE_USER = "user"
MESSAGE_ASSISTANT = "assistant"
MESSAGE_ROLES: tuple[str, ...] = (MESSAGE_USER, MESSAGE_ASSISTANT)

MESSAGE_ACTIVE = "active"
MESSAGE_HIDDEN = "hidden"
MESSAGE_DELETED = "deleted"
MESSAGE_STATUSES: tuple[str, ...] = (MESSAGE_ACTIVE, MESSAGE_HIDDEN, MESSAGE_DELETED)

#: 生成失败时的占位提示：契约要求返回 ``status=failed`` 的正文可读，不留空字符串
FALLBACK_ANSWER = "抱歉，本次回答生成失败，请稍后重试。"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# §一  领域层 dataclass（编排层在 session/message 之间传递）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@dataclass(frozen=True)
class MessageRecord:
    """一条消息的领域快照（``message_id`` 即表里的 ``uuid``）。"""

    message_id: str
    session_id: str
    role: str
    content: str
    citations: list[dict[str, Any]]
    knowledge_deps: list[dict[str, Any]]
    status: str
    created_time: datetime

    @property
    def no_evidence(self) -> bool:
        """这条助手消息当时是否证据不足。

        拒答路径写入的引用与依赖都是空集，正常路径至少有一条证据（``KB_GRADE_MIN_CHUNKS``），
        所以「两者皆空」与 ``no_evidence=true`` 是同一件事，不需要为此多存一列。

        判定放在快照上：契约 #10 历史与 #15 回查都要这个字段，否则同一口径会在两个出口
        各写一遍，改动时漏掉一处就会出现「列表说证据不足、回查说正常」。
        """
        return not self.citations and not self.knowledge_deps


@dataclass(frozen=True)
class MessagePage:
    """一页历史消息：``next_cursor`` 为 None 表示最后一页。"""

    items: tuple[MessageRecord, ...]
    next_cursor: str | None = None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# §二  管理端 Pydantic Schema（/kb/chat-message）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class KbChatMessageOutSchema(BaseSchema):
    """消息列表响应（正文原样回显，引用与依赖以 JSON 展示便于排查）。"""

    session_id: str = Field(default="", description="归属会话 ID")
    role: str = Field(default=MESSAGE_USER, description="user/assistant")
    content: str = Field(default="", description="消息正文")
    citations: list[dict[str, Any]] = Field(default_factory=list, description="引用留痕")
    knowledge_deps: list[dict[str, Any]] = Field(default_factory=list, description="知识依赖")
    status: str = Field(default=MESSAGE_ACTIVE, description="active/hidden/deleted")


class KbChatMessageQueryParam(BaseQueryParam):
    """消息查询参数（``session_id`` 必填，按会话看历史）。"""

    session_id: str | None = Field(None, description="归属会话 ID", json_schema_extra={"q": "eq"})
    role: str | None = Field(None, description="消息角色", json_schema_extra={"q": "eq"})
    status: str | None = Field(None, description="消息状态", json_schema_extra={"q": "eq"})
    content: str | None = Field(None, description="正文关键字", json_schema_extra={"q": "like"})
