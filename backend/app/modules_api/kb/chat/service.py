"""契约会话与生成服务（#08–#15）：校验 → 领域服务 → 契约投影。

本层只做契约相关的事，业务规则一律落在 ``app.modules.kb.chat*``：

1. **身份换算**：契约 ``user_id`` 是「数字主键的字符串形式」，会话表按 ``BIGINT`` 存；
   非数字一律 400 —— 猜一个「差不多的用户」去做权限下推，等于给出别人的知识；
2. **游标**：不透明字符串由本侧签发；非法游标是 400 ``INVALID_REQUEST``，不能漏成 500；
3. **领域异常 → 契约错误码**：不存在 404（他人会话同 404，不泄漏存在性）、
   生成期间被清空/删除 409 ``RESULT_EXPIRED``、权限数据不可用 503、其余依赖故障 500；
4. **结果台账**：#15 的承接方。写操作开始前登记 ``running``、结束时登记最终状态；
   ``result_ref`` 只存「回执除正文外的字段」（DR-08 不存正文全文）——#15 需要答案时按
   ``assistant_message_id`` 回查消息表，顺带还能复核「这条答案现在还应不应该给」。

结果台账只用于「超时之后还能不能查到结果」，它写失败不能反向改主链路响应，
因此收尾失败只记日志（见 ``SystemRequestLogService.finish``）。
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_factory
from app.core.logger import logger
from app.modules.kb.chat.schema import REASON_SOURCE_UNAVAILABLE, ChatTurn, HistoryMessage
from app.modules.kb.chat.service import ChatTurnDiscardedError, KbChatService
from app.modules.kb.chat_message.schema import MESSAGE_ACTIVE, MESSAGE_ASSISTANT
from app.modules.kb.chat_session.schema import SessionRecord
from app.modules.kb.chat_session.service import (
    ChatSessionNotFoundError,
    ChatUserInvalidError,
    KbChatSessionService,
    parse_user_pk,
)
from app.modules.kb.retrieval.service import RetrievalAuthorizationUnavailable, RetrievalEmbeddingUnavailable
from app.modules.system.request_log.service import (
    OPERATION_COMPLETION,
    OPERATION_DELETE,
    OPERATION_MUTATION,
    STATUS_CANCELLED,
    STATUS_EXPIRED,
    STATUS_FAILED,
    STATUS_RUNNING,
    STATUS_SUCCEEDED,
    SystemRequestLogService,
)
from app.utils.cursor import decode_cursor

from ...base.errors import (
    AUTHORIZATION_UNAVAILABLE,
    DEFAULT_MESSAGES,
    ERROR_CODE_STATUS,
    INTERNAL_ERROR,
    INVALID_REQUEST,
    NOT_FOUND,
    RESULT_EXPIRED,
    RETRYABLE_CODES,
    UNSUPPORTED_STREAM,
    ContractApiError,
)
from .schema import (
    COMPLETION_STATUS_COMPLETED,
    STATUS_COMPLETED,
    STATUS_FAILED as CONTRACT_STATUS_FAILED,
    STATUS_PENDING,
    ChatBaseRequest,
    ChatCompletionRequest,
    ChatSessionClearRequest,
    ChatSessionCreateRequest,
    ChatSessionDeleteRequest,
    ChatSessionDetailRequest,
    ChatSessionQueryRequest,
    ChatSessionRenameRequest,
    ChatRequestResultRequest,
)

_LEDGER = SystemRequestLogService()

#: 会话定位失败的统一话术：不存在 / 不属于本人 / 已删除三种情况对调用方是同一件事
_SESSION_NOT_FOUND = "会话不存在或不属于当前用户。"

#: 本期只有一个模型端点；收到别的 ``model_key`` 说明调用方配错了，拒绝比静默换模型诚实
_SUPPORTED_MODEL_KEYS: frozenset[str] = frozenset({"default"})

#: 引用九字段（八元组 + ``citation_id``）与来源六元组：只投影手册列出的键
_CITATION_FIELDS: tuple[str, ...] = (
    "index",
    "citation_id",
    "resource_type",
    "resource_id",
    "publication_id",
    "record_id",
    "version_id",
    "revision",
    "chunk_id",
    "quote",
)
_SNAPSHOT_FIELDS: tuple[str, ...] = (
    "resource_type",
    "resource_id",
    "publication_id",
    "record_id",
    "version_id",
    "revision",
)

#: 台账状态（库内）→ 契约 status（对外）
_LEDGER_TO_CONTRACT_STATUS: dict[str, str] = {
    STATUS_RUNNING: STATUS_PENDING,
    STATUS_SUCCEEDED: STATUS_COMPLETED,
    STATUS_FAILED: CONTRACT_STATUS_FAILED,
    STATUS_CANCELLED: STATUS_CANCELLED,
    STATUS_EXPIRED: STATUS_CANCELLED,
}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# §一  会话：#08 / #09 / #10 / #11 / #12 / #13
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


async def create_session(payload: ChatSessionCreateRequest) -> dict[str, Any]:
    """#08：201 + 会话四字段；同 ``request_id`` 同正文的重试由幂等台账回放首次回执。"""
    user_pk = _user_pk(payload)

    async def action() -> dict[str, Any]:
        async with async_session_factory() as db, db.begin():
            record = await KbChatSessionService(db).create_owned(user_id=user_pk, title=payload.title)
        return _session_payload(payload.request_id, record)

    return await _run_operation(payload.request_id, OPERATION_MUTATION, action)


async def query_sessions(payload: ChatSessionQueryRequest) -> dict[str, Any]:
    """#09：最近活跃优先的游标分页，只返回当前身份的会话。"""
    user_pk = _user_pk(payload)
    _validate_cursor(payload.cursor)
    async with async_session_factory() as db:
        page = await KbChatSessionService(db).page_owned(
            user_id=user_pk,
            cursor=payload.cursor,
            limit=payload.limit,
        )
    return {
        "request_id": payload.request_id,
        "sessions": [_session_item(record) for record in page.items],
        "next_cursor": page.next_cursor,
    }


async def query_session_detail(session_id: str, payload: ChatSessionDetailRequest) -> dict[str, Any]:
    """#10：会话字段 + 可选历史分页；历史逐条按**当前**知识授权判定是否可展示。"""
    user_pk = _user_pk(payload)
    _validate_cursor(payload.cursor)
    async with async_session_factory() as db:
        record = await _session_record(db, user_pk=user_pk, session_id=session_id)

    body = _session_payload(payload.request_id, record)
    if not payload.include_messages:
        return body

    service = KbChatService(user_id=user_pk, tenant_id=payload.tenant_id)
    try:
        page = await service.history(session_id=session_id, cursor=payload.cursor, limit=payload.limit)
    except ChatSessionNotFoundError as exc:
        raise ContractApiError(NOT_FOUND, _SESSION_NOT_FOUND) from exc
    body["messages"] = [_history_message(item) for item in page.items]
    body["next_cursor"] = page.next_cursor
    return body


async def rename_session(session_id: str, payload: ChatSessionRenameRequest) -> dict[str, Any]:
    """#11：只改标题；被删会话不能靠迟到写入复活（软删的记录一律 404）。"""
    user_pk = _user_pk(payload)

    async def action() -> dict[str, Any]:
        try:
            async with async_session_factory() as db, db.begin():
                record = await KbChatSessionService(db).rename_owned(
                    user_id=user_pk,
                    session_id=session_id,
                    title=payload.title,
                )
        except ChatSessionNotFoundError as exc:
            raise ContractApiError(NOT_FOUND, _SESSION_NOT_FOUND) from exc
        return _session_payload(payload.request_id, record)

    return await _run_operation(payload.request_id, OPERATION_MUTATION, action)


async def delete_session(session_id: str, payload: ChatSessionDeleteRequest) -> dict[str, Any]:
    """#12：软删会话与消息；生成中的迟到回答由领域层的行锁判定丢弃。

    台账 ``operation`` 记 ``delete``：#15 查回来能直接看出「这是删除操作的结果」，
    其归属复核也要能越过「会话已不存在」这个前提（``include_deleted``）。
    """
    user_pk = _user_pk(payload)

    async def action() -> dict[str, Any]:
        try:
            async with async_session_factory() as db, db.begin():
                record = await KbChatSessionService(db).delete_owned(user_id=user_pk, session_id=session_id)
        except ChatSessionNotFoundError as exc:
            raise ContractApiError(NOT_FOUND, _SESSION_NOT_FOUND) from exc
        return {"request_id": payload.request_id, "session_id": record.session_id, "deleted": True}

    return await _run_operation(payload.request_id, OPERATION_DELETE, action)


async def clear_messages(session_id: str, payload: ChatSessionClearRequest) -> dict[str, Any]:
    """#13：清空历史、保留会话壳；此后按该会话提问会重新回到 ``active``。"""
    user_pk = _user_pk(payload)

    async def action() -> dict[str, Any]:
        try:
            async with async_session_factory() as db, db.begin():
                record = await KbChatSessionService(db).clear_owned(user_id=user_pk, session_id=session_id)
        except ChatSessionNotFoundError as exc:
            raise ContractApiError(NOT_FOUND, _SESSION_NOT_FOUND) from exc
        return {"request_id": payload.request_id, "session_id": record.session_id, "cleared": True}

    return await _run_operation(payload.request_id, OPERATION_MUTATION, action)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# §二  生成：#14 / #15
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


async def create_completion(payload: ChatCompletionRequest) -> dict[str, Any]:
    """#14：非流式生成。

    ``stream=true`` 在进入检索与模型调用**之前**就拒绝：契约已明确本期不支持流式，
    先拒能省一次昂贵调用，也让调用方立刻知道该改参数。
    """
    if payload.stream:
        raise ContractApiError(UNSUPPORTED_STREAM)
    if payload.model_key.strip() not in _SUPPORTED_MODEL_KEYS:
        raise ContractApiError(
            INVALID_REQUEST,
            f"model_key 暂只支持 {', '.join(sorted(_SUPPORTED_MODEL_KEYS))}，请核对模型标识。",
        )

    user_pk = _user_pk(payload)
    await _LEDGER.start(request_id=payload.request_id, operation=OPERATION_COMPLETION)
    service = KbChatService(user_id=user_pk, tenant_id=payload.tenant_id)
    try:
        turn = await service.completion(
            session_id=payload.session_id,
            question=payload.question,
            prompt_text=payload.prompt_text,
            language=payload.options.language,
            max_tokens=payload.options.max_output_tokens,
        )
    except ChatTurnDiscardedError as exc:
        # 迟到丢弃不是技术失败：会话在生成期间被删除/清空，结果按「已作废」登记
        await _LEDGER.finish(request_id=payload.request_id, status=STATUS_CANCELLED, error_code=RESULT_EXPIRED)
        raise ContractApiError(RESULT_EXPIRED, str(exc)) from exc
    except ChatSessionNotFoundError as exc:
        await _LEDGER.finish(request_id=payload.request_id, status=STATUS_FAILED, error_code=NOT_FOUND)
        raise ContractApiError(NOT_FOUND, _SESSION_NOT_FOUND) from exc
    except RetrievalAuthorizationUnavailable as exc:
        await _LEDGER.finish(request_id=payload.request_id, status=STATUS_FAILED, error_code=AUTHORIZATION_UNAVAILABLE)
        raise ContractApiError(AUTHORIZATION_UNAVAILABLE, str(exc)) from exc
    except RetrievalEmbeddingUnavailable as exc:
        logger.error("#14 问答检索向量化不可用：{}", exc)
        await _LEDGER.finish(request_id=payload.request_id, status=STATUS_FAILED, error_code=INTERNAL_ERROR)
        raise ContractApiError(INTERNAL_ERROR, "向量化依赖暂不可用，请稍后重试。") from exc
    except Exception:
        # 未预期的失败（模型端点 5xx / 超时等）：台账必须收尾，否则 #15 永远显示 pending
        await _LEDGER.finish(request_id=payload.request_id, status=STATUS_FAILED, error_code=INTERNAL_ERROR)
        raise

    body = _completion_body(payload, turn)
    await _LEDGER.finish(request_id=payload.request_id, status=STATUS_SUCCEEDED, result=_completion_ref(body))
    return body


async def query_request_result(target_request_id: str, payload: ChatRequestResultRequest) -> dict[str, Any]:
    """#15：返回**原操作**的实际状态；结果不可展示时给 ``SOURCE_UNAVAILABLE`` 而不是空壳。

    三件事必须在这里做，而不是直接回台账里的 ``result_ref``：

    1. **归属复核**：``request_id`` 是全局键，猜中别人的 ID 不能看到别人的会话，
       不属于自己的按 404 隐藏（与 #10 同口径）；
    2. **可见性复核**：答案依赖的知识可能已撤权、发布单元可能换版、会话可能已清空——
       台账里存着结果不等于现在还能给，助手消息的可见性每次重新判定；
    3. **过期判定**：台账超过可查询期限回 409 ``RESULT_EXPIRED``，不是 404。
    """
    user_pk = _user_pk(payload)
    record = await _LEDGER.lookup(target_request_id)
    if record is None:
        raise ContractApiError(NOT_FOUND, f"请求 {target_request_id} 的操作结果不存在或不可查询。")
    if record.status == STATUS_EXPIRED or record.expired:
        raise ContractApiError(RESULT_EXPIRED)

    body: dict[str, Any] = {
        "request_id": payload.request_id,
        "operation": record.operation,
        "status": _LEDGER_TO_CONTRACT_STATUS.get(record.status, STATUS_PENDING),
    }
    if record.status == STATUS_RUNNING:
        body["result_visible"] = False
        return body
    if record.status == STATUS_CANCELLED:
        body["result_visible"] = False
        return body
    if record.status == STATUS_FAILED:
        body["error"] = _error_detail(record.error_code)
        return body

    result = await _visible_result(record.result, record.result_visible, record.operation, user_pk=user_pk)
    if result is None:
        body["result_visible"] = False
        body["reason"] = REASON_SOURCE_UNAVAILABLE
    else:
        body["result_visible"] = True
        body["result"] = result
    return body


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# §三  内部：台账 / 归属 / 投影
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


async def _run_operation(
    request_id: str,
    operation: str,
    action: Callable[[], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """写操作统一口径：先登记 ``running``，成功存回执、失败存失败码，然后原样上抛。"""
    await _LEDGER.start(request_id=request_id, operation=operation)
    try:
        body = await action()
    except ContractApiError as exc:
        await _LEDGER.finish(request_id=request_id, status=STATUS_FAILED, error_code=exc.code)
        raise
    except Exception:
        await _LEDGER.finish(request_id=request_id, status=STATUS_FAILED, error_code=INTERNAL_ERROR)
        raise
    await _LEDGER.finish(request_id=request_id, status=STATUS_SUCCEEDED, result=body)
    return body


async def _visible_result(
    ref: dict[str, Any] | None,
    result_visible: bool,
    operation: str,
    *,
    user_pk: int,
) -> dict[str, Any] | None:
    """台账结果 → 可返回结果；归属不符按 404，仍然有效但不可展示返回 None。"""
    if not result_visible or not ref:
        return None
    session_id = str(ref.get("session_id") or "")
    if not session_id:
        return None

    # #15 只做可见性复核，不检索：租户参数在这里没有意义
    service = KbChatService(user_id=user_pk, tenant_id="")
    if not await service.owns_session(session_id, include_deleted=operation != OPERATION_COMPLETION):
        raise ContractApiError(NOT_FOUND, _SESSION_NOT_FOUND)
    if operation != OPERATION_COMPLETION:
        return dict(ref)

    assistant = await service.message(str(ref.get("assistant_message_id") or ""))
    if assistant is None or assistant.status != MESSAGE_ACTIVE or assistant.session_id != session_id:
        # 清空 / 删除会把消息置为 deleted：台账有结果，但现在不能再展示
        return None
    if not (await service.dependency_visibility([assistant.knowledge_deps]))[0]:
        return None

    result = dict(ref)
    result["assistant_message_id"] = assistant.message_id
    result["status"] = COMPLETION_STATUS_COMPLETED
    result["answer"] = assistant.content
    return result


async def _session_record(db: AsyncSession, *, user_pk: int, session_id: str) -> SessionRecord:
    """按 ``session_id + user_id`` 定位会话；不存在与不属于本人同口径 404。"""
    try:
        return await KbChatSessionService(db).get_owned(user_id=user_pk, session_id=session_id)
    except ChatSessionNotFoundError as exc:
        raise ContractApiError(NOT_FOUND, _SESSION_NOT_FOUND) from exc


def _session_payload(request_id: str, record: SessionRecord) -> dict[str, Any]:
    """#08 / #11 响应，也是 #15 ``mutation`` 分支的 result。"""
    return {
        "request_id": request_id,
        "session_id": record.session_id,
        "title": record.title,
        "created_at": _iso(record.created_time),
        "updated_at": _iso(record.updated_time),
    }


def _session_item(record: SessionRecord) -> dict[str, Any]:
    """#09 列表项：``request_id`` 在最外层，不在每个元素里重复。"""
    return {
        "session_id": record.session_id,
        "title": record.title,
        "created_at": _iso(record.created_time),
        "updated_at": _iso(record.updated_time),
    }


def _history_message(item: HistoryMessage) -> dict[str, Any]:
    """历史消息投影：不可展示时只回 ID / 角色 / 时间 / 原因，正文与引用一律不返回。"""
    record = item.record
    body: dict[str, Any] = {
        "message_id": record.message_id,
        "role": record.role,
        "content_visible": item.content_visible,
        "created_at": _iso(record.created_time),
    }
    if not item.content_visible:
        body["reason"] = item.reason or REASON_SOURCE_UNAVAILABLE
        return body

    body["content"] = record.content
    if record.role == MESSAGE_ASSISTANT:
        # 助手消息必须带齐引用与来源依赖：GSI 会逐项再授权，缺一项就得整条隐藏
        body["citations"] = [_project(citation, _CITATION_FIELDS) for citation in record.citations]
        # 来源六元组从引用投影：写库时每条证据都已落到 citations（未过滤），两者覆盖同一批来源；
        # ``knowledge_deps`` 只存依赖三元组（用于失效判定），缺 resource_type / version_id，
        # 单靠它无法还原完整快照
        body["source_snapshots"] = [_project(citation, _SNAPSHOT_FIELDS) for citation in record.citations]
        body["no_evidence"] = record.no_evidence
    return body


def _completion_body(payload: ChatCompletionRequest, turn: ChatTurn) -> dict[str, Any]:
    """#14 响应正文（#15 ``completion`` 分支在此基础上按台账补回 ``answer``）。"""
    return {
        "request_id": payload.request_id,
        "session_id": turn.session_id,
        "user_message_id": turn.user_message_id,
        "assistant_message_id": turn.assistant_message_id,
        "status": COMPLETION_STATUS_COMPLETED,
        "answer": turn.answer,
        "citations": [dict(citation) for citation in turn.citations],
        "source_snapshots": [dict(snapshot) for snapshot in turn.source_snapshots],
        "authorization_scope_id": payload.authorization.scope_id,
        "no_evidence": turn.no_evidence,
        "finish_reason": turn.finish_reason,
    }


def _completion_ref(body: dict[str, Any]) -> dict[str, Any]:
    """台账只存回执里除正文之外的字段（DR-08 不存全文）；答案由 #15 回查消息表补回。"""
    return {key: value for key, value in body.items() if key != "answer"}


def _project(raw: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    """按手册字段清单投影：只回契约列出的键，内部字段不外泄。"""
    return {field: raw.get(field) for field in fields}


def _error_detail(code: str | None) -> dict[str, Any]:
    """失败分支的错误体：码不在册一律按 ``INTERNAL_ERROR``，话术与 retryable 同源推导。"""
    resolved = code if code in ERROR_CODE_STATUS else INTERNAL_ERROR
    return {
        "code": resolved,
        "message": DEFAULT_MESSAGES.get(resolved, DEFAULT_MESSAGES[INTERNAL_ERROR]),
        "retryable": resolved in RETRYABLE_CODES,
    }


def _user_pk(payload: ChatBaseRequest) -> int:
    """契约 ``user_id``（数字字符串）→ 会话表的 ``BIGINT`` 归属键。

    校验在 ``chat_session.parse_user_pk``（同一份口径服务契约与管理端），本层只把领域异常
    翻成契约码：``user_id`` 非数字属于请求格式错误，不能用 500 或静默建会话。
    """
    try:
        return parse_user_pk(payload.user_id)
    except ChatUserInvalidError as exc:
        raise ContractApiError(INVALID_REQUEST, str(exc)) from exc


def _validate_cursor(cursor: str | None) -> None:
    """游标先校验再交给领域层：领域层抛的是 ``ValueError``，漏出去会变成 500。"""
    try:
        decode_cursor(cursor)
    except ValueError as exc:
        raise ContractApiError(INVALID_REQUEST, str(exc)) from exc


def _iso(value: datetime | None) -> str:
    """时间统一 ``YYYY-MM-DDTHH:MM:SSZ``：手册全部示例都是 UTC 秒级带 Z。"""
    if value is None:
        return ""
    moment = value if value.tzinfo else value.replace(tzinfo=UTC)
    return moment.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
