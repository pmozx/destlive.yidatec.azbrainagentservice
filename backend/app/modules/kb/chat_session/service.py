"""问答会话服务：管理端维护链路 + 契约 #08–#13 的归属链路。

两条链路的差别只有归属校验，其余动作完全一致，所以共享同一批私有方法：

- **管理端**（``/kb/chat-session``）：管理员看全量，按 ``session_id`` 直接操作，不校验归属；
- **契约链路**（``/v1/chat/sessions``）：一切操作都先按 ``user_id`` 收窄，
  不属于自己的会话与不存在的会话返回**同一个**结果，避免用 404/403 的差异探测他人会话。

异常口径：领域层只抛 :class:`ChatSessionNotFoundError`，HTTP 状态码由调用方决定
（管理端转 404，契约层转 ``NOT_FOUND``），避免领域层依赖 FastAPI 的语义。
"""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_schema import PageResultSchema
from app.utils.common_util import search_to_dict
from app.utils.cursor import decode_cursor, encode_cursor

from ..chat_message.crud import KbChatMessageCRUD
from .crud import KbChatSessionCRUD
from .model import KbChatSessionModel
from .schema import (
    DEFAULT_SESSION_TITLE,
    SESSION_ACTIVE,
    SESSION_DELETED,
    KbChatSessionDetailOutSchema,
    KbChatSessionOutSchema,
    KbChatSessionQueryParam,
    KbChatSessionUpdateSchema,
    SessionPage,
    SessionRecord,
)


class ChatSessionNotFoundError(Exception):
    """会话不存在、不属于该用户或已删除（三种情况对调用方是同一件事）。"""


class KbChatSessionService:
    """问答会话服务（管理端维护 + 契约归属链路）。"""

    #: 契约字段 → 表列名：``session_id`` 就是 ``uuid``。通用 CRUD 按「字段名 == 列名」拼条件，
    #: 不在 ``_build_conditions`` 里映射，就会被它的 ``hasattr`` 兜底静默跳过（筛选填了等于没填）。
    _SEARCH_COLUMN_ALIASES = {"session_id": "uuid"}

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.crud = KbChatSessionCRUD(db)
        self.message_crud = KbChatMessageCRUD(db)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # §一  管理端（/kb/chat-session）
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def page(
        self,
        *,
        page_no: int,
        page_size: int,
        search: KbChatSessionQueryParam | None = None,
        order_by: list[dict[str, str]] | None = None,
    ) -> PageResultSchema[KbChatSessionOutSchema]:
        """管理端分页：单表查询，支持会话 ID / 用户 / 标题 / 状态过滤。

        **刻意不用通用分页的 ``out_schema``**：本表契约字段 ``session_id`` 由列 ``uuid``
        投影，而通用分页是 ``model_validate(orm_row)``——ORM 行没有 ``session_id`` 属性，
        会静默取默认值空串（列表页整个「会话 ID」列空白，而详情/改名走 :meth:`to_out`
        却有值）。这里与其它出口统一走 ``to_out(to_record(row))``，只保留一种映射口径。
        筛选条件同理按 :attr:`_SEARCH_COLUMN_ALIASES` 把 ``session_id`` 换到 ``uuid`` 列上。
        """
        conditions = search_to_dict(search) or {}
        for field, column in self._SEARCH_COLUMN_ALIASES.items():
            if field in conditions:
                conditions[column] = conditions.pop(field)
        result = await self.crud.page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=order_by or [{"last_active_at": "desc"}, {"id": "desc"}],
            search=conditions,
        )
        result.items = [self.to_out(self.to_record(row)) for row in result.items]
        return result

    async def detail(self, session_id: str) -> KbChatSessionDetailOutSchema:
        """管理端详情：会话字段 + 未删除消息总数（正文走消息列表分页取）。"""
        session = await self._ensure_session(session_id)
        return KbChatSessionDetailOutSchema(
            **self.to_out(self.to_record(session)).model_dump(),
            message_total=await self.message_crud.count_visible(session_id),
        )

    async def update(self, session_id: str, data: KbChatSessionUpdateSchema) -> KbChatSessionOutSchema:
        """管理端改标题：只改标题，不动消息与状态。"""
        session = await self._ensure_session(session_id)
        session.title = data.title
        await self.db.flush()
        await self.db.refresh(session)
        return self.to_out(self.to_record(session))

    async def clear(self, session_id: str) -> KbChatSessionOutSchema:
        """管理端清空历史：消息置 ``deleted``，会话转 ``cleared`` 并归零消息数。"""
        session = await self._ensure_session(session_id)
        await self._clear(session)
        return self.to_out(self.to_record(session))

    async def delete(self, session_id: str) -> None:
        """管理端删除会话（软删）：消息一并置 ``deleted``，会话不再可见。"""
        session = await self._ensure_session(session_id)
        await self._delete(session)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # §二  契约链路（/v1/chat/sessions，#08–#13）
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def create_owned(self, *, user_id: int, title: str | None = None) -> SessionRecord:
        """#08 创建会话：未给标题时用默认标题，首轮提问后由编排层改写。"""
        now = datetime.now(UTC)
        session = await self.crud.create(
            data={
                "user_id": user_id,
                "title": (title or "").strip() or DEFAULT_SESSION_TITLE,
                "message_count": 0,
                "last_active_at": now,
                "status": SESSION_ACTIVE,
            }
        )
        return self.to_record(session)

    async def page_owned(self, *, user_id: int, cursor: str | None, limit: int) -> SessionPage:
        """#09 分页查询我的会话：最近活跃优先，游标为不透明字符串。

        多取一条判断是否还有下一页——比再发一次 COUNT 便宜，也不会因为并发新增
        让 ``next_cursor`` 与实际页数错位。
        """
        offset = decode_cursor(cursor)
        rows, _ = await self.crud.page_owned(user_id=user_id, offset=offset, limit=limit + 1)
        has_next = len(rows) > limit
        items = tuple(self.to_record(row) for row in rows[:limit])
        return SessionPage(items=items, next_cursor=encode_cursor(offset + limit) if has_next else None)

    async def get_owned(self, *, user_id: int, session_id: str) -> SessionRecord:
        """#10 会话详情（消息正文由编排层/消息服务补充）。"""
        return self.to_record(await self._ensure_owned(session_id=session_id, user_id=user_id))

    async def rename_owned(self, *, user_id: int, session_id: str, title: str) -> SessionRecord:
        """#11 改标题：归属校验后只改标题。"""
        session = await self._ensure_owned(session_id=session_id, user_id=user_id)
        session.title = title
        await self.db.flush()
        await self.db.refresh(session)
        return self.to_record(session)

    async def clear_owned(self, *, user_id: int, session_id: str) -> SessionRecord:
        """#13 清空历史：会话壳保留，历史消息不再展示。"""
        session = await self._ensure_owned(session_id=session_id, user_id=user_id)
        await self._clear(session)
        return self.to_record(session)

    async def delete_owned(self, *, user_id: int, session_id: str) -> SessionRecord:
        """#12 删除会话：返回删除前的快照（契约只需要 ``session_id``）。"""
        session = await self._ensure_owned(session_id=session_id, user_id=user_id)
        record = self.to_record(session)
        await self._delete(session)
        return record

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # §三  私有
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def _ensure_session(self, session_id: str) -> KbChatSessionModel:
        """管理端定位：只要行存在且未删除即可。"""
        session = await self.crud.get_by_session_id(session_id)
        if session is None or session.status == SESSION_DELETED:
            raise ChatSessionNotFoundError("会话不存在")
        return session

    async def _ensure_owned(self, *, session_id: str, user_id: int) -> KbChatSessionModel:
        """契约定位：``session_id`` 与 ``user_id`` 必须同时匹配，否则一律 404。"""
        session = await self.crud.get_owned(session_id=session_id, user_id=user_id)
        if session is None:
            raise ChatSessionNotFoundError("会话不存在")
        return session

    async def _clear(self, session: KbChatSessionModel) -> None:
        """清空历史：消息软删 + 会话置 ``cleared`` + 消息数归零。"""
        await self.message_crud.mark_session_deleted(session.uuid)
        await self.crud.mark_cleared(session_id=session.uuid, active_at=datetime.now(UTC))
        await self.db.refresh(session)

    async def _delete(self, session: KbChatSessionModel) -> None:
        """删除会话：消息先软删（避免残留可查的孤儿消息），再软删会话。"""
        await self.message_crud.mark_session_deleted(session.uuid)
        await self.crud.mark_deleted(session_id=session.uuid)

    # ── 转换 ──────────────────────────────────────────────────────────

    @staticmethod
    def to_record(session: KbChatSessionModel) -> SessionRecord:
        """模型 → 领域快照（``session_id`` 由 ``uuid`` 投影，不暴露自增主键）。"""
        return SessionRecord(
            session_id=session.uuid,
            user_id=session.user_id,
            title=session.title or DEFAULT_SESSION_TITLE,
            message_count=session.message_count or 0,
            status=session.status or SESSION_ACTIVE,
            created_time=session.created_time,
            updated_time=session.updated_time,
            last_active_at=session.last_active_at,
        )

    @staticmethod
    def to_out(record: SessionRecord) -> KbChatSessionOutSchema:
        """领域快照 → 管理端响应（``uuid`` 位填 ``session_id``，与契约同口径）。"""
        return KbChatSessionOutSchema(
            uuid=record.session_id,
            session_id=record.session_id,
            user_id=record.user_id,
            title=record.title,
            message_count=record.message_count,
            last_active_at=record.last_active_at,
            status=record.status,
            created_time=record.created_time,
            updated_time=record.updated_time,
        )
