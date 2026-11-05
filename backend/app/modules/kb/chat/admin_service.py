"""AI 对话管理端服务：让管理员在页面上以指定用户身份试问一轮问答。

与契约 #14 的差别只有三处，其余口径完全一致（同一个 ``KbChatService``、同一批会话与消息表）：

1. **不占幂等键**：页面是人在点，重复点击就是两次真实提问，没有「同 request_id 回放」的语义；
2. **没有签名五头与授权时效块**：走管理端登录态，身份靠显式 ``user_id``；
3. **异常直接翻成 HTTP**：会话不属于该用户 → 404，生成期间被清空/删除 → 409，
   检索依赖（授权数据 / 向量化）不可用 → 503，都是页面上能直接看懂的语义。

两个出口共用同一轮编排：``ask`` 一次性返回 JSON，``open_stream`` 返回 SSE 帧流。
写出来的会话与消息和契约链路同表同口径，因此会同步出现在「会话记录」页——
AI 对话页负责产生，会话记录页负责按会话查看与维护。
"""

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from time import perf_counter
from typing import Any

from fastapi import status as http_status

from app.core.database import async_session_factory
from app.core.exceptions import CustomException
from app.core.logger import logger

from ..chat_session.service import (
    ChatSessionNotFoundError,
    ChatUserInvalidError,
    KbChatSessionService,
    parse_user_pk,
)
from ..retrieval.service import RetrievalAuthorizationUnavailable, RetrievalEmbeddingUnavailable
from .schema import (
    ADMIN_DEFAULT_PROMPT_TEXT,
    KbChatAskOutSchema,
    KbChatAskSchema,
    KbChatCitationOutSchema,
)
from .service import ChatTurnDiscardedError, KbChatService


class KbChatAdminService:
    """AI 对话管理端服务（无请求级会话：编排层自己开事务）。"""

    async def ask(self, data: KbChatAskSchema) -> KbChatAskOutSchema:
        """一轮问答：缺会话先建，再交给编排层检索 → 生成 → 落库。"""
        user_pk = self._user_pk(data.user_id)
        session_id = (data.session_id or "").strip() or await self._create_session(user_pk)
        started = perf_counter()
        try:
            turn = await KbChatService(user_id=user_pk, tenant_id=data.tenant_id).completion(
                session_id=session_id,
                question=data.question,
                prompt_text=(data.prompt_text or "").strip() or ADMIN_DEFAULT_PROMPT_TEXT,
                language=data.language,
                max_tokens=data.max_output_tokens,
            )
        except ChatSessionNotFoundError as exc:
            # 会话不存在与「不属于该用户」同一个出口：页面改一下用户 ID 就看不到别人的会话
            raise CustomException(msg=str(exc), status_code=http_status.HTTP_404_NOT_FOUND) from exc
        except ChatTurnDiscardedError as exc:
            raise CustomException(msg=str(exc), status_code=http_status.HTTP_409_CONFLICT) from exc
        except (RetrievalAuthorizationUnavailable, RetrievalEmbeddingUnavailable) as exc:
            # 与 #07 同一口径：依赖故障如实 503，不拿空 citations 冒充「没有依据」
            raise CustomException(msg=str(exc), status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE) from exc

        return KbChatAskOutSchema(
            session_id=turn.session_id,
            user_message_id=turn.user_message_id,
            assistant_message_id=turn.assistant_message_id,
            answer=turn.answer,
            no_evidence=turn.no_evidence,
            finish_reason=turn.finish_reason,
            elapsed_ms=round((perf_counter() - started) * 1000),
            citations=[KbChatCitationOutSchema.model_validate(item) for item in turn.citations],
        )

    # ── 流式（SSE）──────────────────────────────────────────────────────

    async def open_stream(
        self,
        data: KbChatAskSchema,
        *,
        is_disconnected: Callable[[], Awaitable[bool]] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """返回一轮问答的 SSE 帧流。

        这是普通 ``async`` 方法（不是 async generator）：身份校验、会话归属校验、首轮建会话
        都在返回帧流之前完成，因此失败就是干净的 HTTP 4xx/5xx；一旦把校验写进生成器，
        函数体要等第一次迭代才执行——那时 200 与 ``text/event-stream`` 已经发出去了，
        再抛异常只能变成流里的畸形字节（规范 S5）。
        """
        user_pk = self._user_pk(data.user_id)
        service = KbChatService(user_id=user_pk, tenant_id=data.tenant_id)
        session_id = (data.session_id or "").strip()
        if session_id:
            # 归属在首帧前就判掉：不属于自己的会话与不存在同一个出口，不泄露存在性
            if not await service.owns_session(session_id):
                raise CustomException(msg="会话不存在", status_code=http_status.HTTP_404_NOT_FOUND)
        else:
            # meta 帧要把 session_id 发出去，只能先建；失败时留下空会话壳，由会话记录页清理
            session_id = await self._create_session(user_pk)

        request_id = uuid.uuid4().hex
        logger.info("AI 对话流式开始：request_id={} user_id={} session={}", request_id, user_pk, session_id)
        return service.completion_stream(
            session_id=session_id,
            question=data.question,
            prompt_text=(data.prompt_text or "").strip() or ADMIN_DEFAULT_PROMPT_TEXT,
            request_id=request_id,
            language=data.language,
            max_tokens=data.max_output_tokens,
            is_disconnected=is_disconnected,
        )

    # ── 内部 ──────────────────────────────────────────────────────────

    @staticmethod
    async def _create_session(user_pk: int) -> str:
        """首轮提问自动建会话：标题留空由领域层按问题派生，页面不必先点「新建对话」。"""
        async with async_session_factory() as db, db.begin():
            record = await KbChatSessionService(db).create_owned(user_id=user_pk)
        return record.session_id

    @staticmethod
    def _user_pk(user_id: str) -> int:
        """``user_id``（数字字符串）→ 会话归属键。

        校验在 ``chat_session.parse_user_pk``，与契约 #08–#14 同一份口径；这里只把领域异常
        翻成管理端的 400。
        """
        try:
            return parse_user_pk(user_id)
        except ChatUserInvalidError as exc:
            raise CustomException(msg=str(exc), status_code=http_status.HTTP_400_BAD_REQUEST) from exc
