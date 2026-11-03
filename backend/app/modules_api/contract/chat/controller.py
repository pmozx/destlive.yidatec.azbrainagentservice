"""`/v1` 会话与生成接口（#08–#15）：controller 只做参数接收、调 service、包响应。

幂等域按契约 C3 分工：

- **请求域**（`idempotent_request`）：#08 / #11 / #14 —— 同 `request_id` 同正文回放首次回执；
- **事件域**（`idempotent_event`）：#12 / #13 —— DELETE 带 JSON 正文，键取正文里的 `request_id`，
  删除类另留幂等墓碑（`expires_at=NULL`），防止「删除后重发同一 ID」把已删内容复活；
- **读接口**：#09 / #10 / #15 不占幂等键，重复查询本来就该返回同一事实。

业务编排在 ``service.py``；会话 / 消息 / 问答的落库在 ``app.modules.kb.chat_session|chat_message|chat``。
`response_model_exclude_none=True` 用于「条件必返」字段：本场景不适用的字段整体省略，
而不是回一个 `null` 或空串（手册对 `content` / `citations` / `reason` 的要求）。
"""

from typing import Annotated

from fastapi import Depends, Request, status

from ..common import make_contract_router
from ..deps import idempotent_event, idempotent_request, record_receipt, replay_response
from ..idempotency import Reservation
from .schema import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatRequestResultRequest,
    ChatRequestResultResponse,
    ChatSessionClearReceipt,
    ChatSessionClearRequest,
    ChatSessionCreateRequest,
    ChatSessionDeleteReceipt,
    ChatSessionDeleteRequest,
    ChatSessionDetailRequest,
    ChatSessionDetailResponse,
    ChatSessionListResponse,
    ChatSessionPayload,
    ChatSessionQueryRequest,
    ChatSessionRenameRequest,
)
from .service import (
    clear_messages,
    create_completion,
    create_session,
    delete_session,
    query_request_result,
    query_session_detail,
    query_sessions,
    rename_session,
)

ChatRouter = make_contract_router(prefix="/chat", tags=["契约 /v1 会话与生成（#08–#15）"])


# ── 会话：#08 / #09 / #10 / #11 / #12 / #13 ──────────────────────────


@ChatRouter.post(
    "/sessions",
    status_code=status.HTTP_201_CREATED,
    summary="#08 创建新会话",
    response_model=ChatSessionPayload,
)
async def create_chat_session(
    request: Request,
    reservation: Annotated[Reservation, Depends(idempotent_request)],
    payload: ChatSessionCreateRequest,
):
    """`session_id` 由 AI 分配；不传标题时用默认「新对话」，首轮提问后自动改写。"""
    replayed = replay_response(reservation)
    if replayed is not None:
        return replayed
    body = await create_session(payload)
    await record_receipt(request, reservation, status_code=status.HTTP_201_CREATED, body=body)
    return body


@ChatRouter.post(
    "/sessions/query",
    summary="#09 分页查询我的会话",
    response_model=ChatSessionListResponse,
)
async def query_chat_sessions(payload: ChatSessionQueryRequest):
    """游标不透明（`next_cursor` 不能自行拼接）；只返回签名身份自己的会话。"""
    return await query_sessions(payload)


@ChatRouter.post(
    "/sessions/{session_id}/query",
    summary="#10 会话详情（含历史分页）",
    response_model=ChatSessionDetailResponse,
    response_model_exclude_none=True,
)
async def query_chat_session_detail(session_id: str, payload: ChatSessionDetailRequest):
    """`include_messages=false` 只回会话字段；历史消息逐条按最新知识授权判定可见性。"""
    return await query_session_detail(session_id, payload)


@ChatRouter.patch(
    "/sessions/{session_id}",
    summary="#11 修改会话标题",
    response_model=ChatSessionPayload,
)
async def rename_chat_session(
    request: Request,
    session_id: str,
    reservation: Annotated[Reservation, Depends(idempotent_request)],
    payload: ChatSessionRenameRequest,
):
    """标题最多 100 字符；他人会话按 404 隐藏存在性。"""
    replayed = replay_response(reservation)
    if replayed is not None:
        return replayed
    body = await rename_session(session_id, payload)
    await record_receipt(request, reservation, status_code=status.HTTP_200_OK, body=body)
    return body


@ChatRouter.delete(
    "/sessions/{session_id}",
    summary="#12 删除会话",
    response_model=ChatSessionDeleteReceipt,
)
async def delete_chat_session(
    request: Request,
    session_id: str,
    reservation: Annotated[Reservation, Depends(idempotent_event)],
    payload: ChatSessionDeleteRequest,
):
    """DELETE 带 JSON 正文；生成中的迟到回答会被行锁判定丢弃，不会把会话写活。"""
    replayed = replay_response(reservation)
    if replayed is not None:
        return replayed
    body = await delete_session(session_id, payload)
    await record_receipt(request, reservation, status_code=status.HTTP_200_OK, body=body, tombstone=True)
    return body


@ChatRouter.delete(
    "/sessions/{session_id}/messages",
    summary="#13 清空会话历史",
    response_model=ChatSessionClearReceipt,
)
async def clear_chat_messages(
    request: Request,
    session_id: str,
    reservation: Annotated[Reservation, Depends(idempotent_event)],
    payload: ChatSessionClearRequest,
):
    """保留会话壳；清空后迟到的生成结果直接丢弃，旧回答不再可见。"""
    replayed = replay_response(reservation)
    if replayed is not None:
        return replayed
    body = await clear_messages(session_id, payload)
    await record_receipt(request, reservation, status_code=status.HTTP_200_OK, body=body)
    return body


# ── 生成：#14 / #15 ──────────────────────────────────────────────────


@ChatRouter.post(
    "/completions",
    summary="#14 发送问题并生成回答（非流式）",
    response_model=ChatCompletionResponse,
)
async def create_chat_completion(
    request: Request,
    reservation: Annotated[Reservation, Depends(idempotent_request)],
    payload: ChatCompletionRequest,
):
    """`stream=true` → 400 `UNSUPPORTED_STREAM`；检索、生成、落库与结果台账都在 service 内完成。"""
    replayed = replay_response(reservation)
    if replayed is not None:
        return replayed
    body = await create_completion(payload)
    await record_receipt(request, reservation, status_code=status.HTTP_200_OK, body=body)
    return body


@ChatRouter.post(
    "/requests/{target_request_id}/query",
    summary="#15 查询超时操作的结果",
    response_model=ChatRequestResultResponse,
    response_model_exclude_none=True,
)
async def query_chat_request(target_request_id: str, payload: ChatRequestResultRequest):
    """正文 `request_id` 是本次查询的新 ID，路径 `target_request_id` 是原操作 ID。"""
    return await query_request_result(target_request_id, payload)
