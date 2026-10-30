"""问答服务：SSE 流式问答入口、问答范围清单、答案反馈。

帧序列化在此收口，链路层（``pipeline``）只产出 ``{"event", "data"}`` 字典，
两处职责分开后，改帧格式不必改动检索/生成逻辑。
"""

from collections.abc import AsyncGenerator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_factory
from app.core.logger import logger
from app.modules.integration_kb.retrieval import space_option_list
from app.utils.sse import format_sse

from . import prompt
from .pipeline import run_chat_pipeline
from .schema import ChatStreamInSchema
from .session.service import IntegrationChatSessionService


class IntegrationChatService:
    """问答服务（无 RBAC：调用方即使用端，问答范围只按「启用空间」约束）"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def scope_options(self) -> list[dict]:
        """问答范围选择器数据（仅启用空间；全部空间问答时前端拿它展示名称）。"""
        return await space_option_list(self.db)

    async def set_feedback(self, message_id: int, feedback: int) -> None:
        """记录答案反馈（1 有用 / -1 无用）。"""
        await IntegrationChatSessionService(self.db).set_feedback(message_id=message_id, feedback=feedback)


async def stream_chat(data: ChatStreamInSchema, request: Request) -> AsyncGenerator[str, None]:
    """执行一次流式问答，产出可直接写回的 SSE 文本帧。

    不依赖请求级会话：SSE 生命周期远长于一次普通请求，不能把事务一直挂着；
    这里自开一个只读会话供检索使用，落库（user / assistant 消息）
    由 ``conversation`` 另开短事务，与此会话无关。
    """
    try:
        async with async_session_factory() as db:
            async for event in run_chat_pipeline(request=request, db=db, data=data):
                yield format_sse(event)
    except Exception as exc:  # noqa: BLE001 兜底：链路崩溃也要给前端一个可读的收尾帧
        logger.error(f"问答流式链路异常终止：{exc}", exc_info=True)
        yield format_sse({"event": "error", "data": {"code": "internalError", "message": prompt.INTERNAL_ERROR_MESSAGE}})
        yield format_sse({"event": "done", "data": {"messageId": 0, "usage": {"tokens_in": 0, "tokens_out": 0}}})
