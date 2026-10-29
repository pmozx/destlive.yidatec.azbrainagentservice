"""会话与消息的落库编排（流式链路专用，一律用独立短事务）。

为什么不用请求级会话：请求级事务要等响应结束才提交，SSE 断流反而会回滚，
「用户这一问没存上」是最不能接受的失败。因此本模块自开 ``async_session_factory``
短事务，逐条提交：

1. 历史必须在落 user 消息**之前**读取，否则本轮提问会混进历史；
2. assistant 落库失败重试一次，仍失败则返回 0（前端据此禁用反馈入口）；
3. 引用计数累加失败只告警，不影响问答结论。
"""

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_factory
from app.core.logger import logger
from app.modules.integration_kb.retrieval import touch_documents

from .session.crud import IntegrationChatMessageCRUD, IntegrationChatSessionCRUD
from .session.schema import MessageCreateSchema

# 消息角色（role：1 用户 2 助手）
ROLE_USER, ROLE_ASSISTANT = 1, 2


@asynccontextmanager
async def write_session() -> AsyncIterator[AsyncSession]:
    """独立写会话：出块即提交，异常回滚（不占用请求级长事务）。"""
    async with async_session_factory() as session, session.begin():
        yield session


def build_title(question: str) -> str:
    """由首问生成会话标题（取前 20 字，空则「新对话」）。"""
    return (question or "").strip()[:20] or "新对话"


async def prepare_turn(
    *,
    session_id: int | None,
    question: str,
    scope_type: int,
    space_ids: Sequence[int] | None,
    turns: int,
) -> tuple[int, list[dict[str, str]]]:
    """解析会话 → 读取历史 → 落 user 消息，返回 ``(会话ID, 历史消息)``。"""
    async with write_session() as db:
        session_crud = IntegrationChatSessionCRUD(db)
        message_crud = IntegrationChatMessageCRUD(db)
        if session_id:
            session = await session_crud.get(id=session_id)
            if session is None:
                raise ValueError("问答会话不存在，请刷新会话列表后重试")
        else:
            session = await session_crud.create(
                data={
                    "title": build_title(question),
                    "scope_type": scope_type,
                    "space_ids": list(space_ids) if space_ids else None,
                    "message_count": 0,
                }
            )
        # 历史先读后写：本轮提问不能出现在自己的上下文里
        recent = await message_crud.get_recent(session_id=session.id, limit=max(0, turns) * 2)
        history = [
            {"role": "user" if item.role == ROLE_USER else "assistant", "content": item.content}
            for item in recent
        ]
        await message_crud.create(data=MessageCreateSchema(session_id=session.id, role=ROLE_USER, content=question))
        await session_crud.update(
            id=session.id,
            data={"message_count": (session.message_count or 0) + 1, "last_message_time": datetime.now(UTC)},
        )
        return session.id, history


async def save_assistant(session_id: int, payload: MessageCreateSchema) -> int:
    """落 assistant 消息（失败重试一次）；返回消息ID，0 表示落库失败。"""
    for attempt in (1, 2):
        try:
            async with write_session() as db:
                message = await IntegrationChatMessageCRUD(db).create(data=payload)
                session = await IntegrationChatSessionCRUD(db).get(id=session_id)
                if session is not None:
                    await IntegrationChatSessionCRUD(db).update(
                        id=session_id,
                        data={
                            "message_count": (session.message_count or 0) + 1,
                            "last_message_time": datetime.now(UTC),
                        },
                    )
                return int(message.id)
        except Exception as exc:  # noqa: BLE001 落库失败不阻断 done 帧
            if attempt == 1:
                logger.warning(f"assistant 消息落库失败，重试一次：session={session_id} {exc}")
                continue
            logger.error(f"assistant 消息落库失败（messageId=0）：session={session_id} {exc}")
    return 0


async def touch_cited_documents(doc_ids: Sequence[int]) -> None:
    """累加被引用文档的引用次数（统计口径，失败不影响回答）。"""
    ids = [int(doc_id) for doc_id in doc_ids if doc_id]
    if not ids:
        return
    async with write_session() as db:
        await touch_documents(db, ids)


def to_history_messages(history: Sequence[dict[str, str]]) -> list[dict[str, str]]:
    """历史消息 → OpenAI 兼容消息体（助手侧截断，历史不作为知识来源）。"""
    from .prompt import trimming_history_text

    messages: list[dict[str, str]] = []
    for item in history:
        content = (item.get("content") or "").strip()
        if not content:
            continue
        if item.get("role") == "assistant":
            messages.append({"role": "assistant", "content": trimming_history_text(content)})
        else:
            messages.append({"role": "user", "content": content})
    return messages


__all__ = [
    "ROLE_ASSISTANT",
    "ROLE_USER",
    "build_title",
    "prepare_turn",
    "save_assistant",
    "to_history_messages",
    "touch_cited_documents",
    "write_session",
]
