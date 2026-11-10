"""AI 对话 Controller：管理端试问台（一次性返回 + SSE 流式两个出口）。

路由前缀 ``/kb/chat``（域前缀由 ``app/api/v1/routers.py`` 挂载），整组接口挂
``ApiLogRoute`` 自动留痕。与「会话记录」页的分工：这里产生会话与消息，那边按会话
查看与维护；写库走的是与契约 #14 同一个编排服务，所以两边看到的是同一批数据。

``/ask/stream`` 的帧契约由 `sse.py` 定义：帧构造在 `sse.py`、序列化在 `app/utils/sse.format_sse`，本文件只负责拼响应头。
"""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.common.response import ResponseSchema, SuccessResponse
from app.core.router_class import ApiLogRoute
from app.utils.sse import SSE_HEADERS, SSE_MEDIA_TYPE, format_sse

from .admin_service import KbChatAdminService
from .schema import KbChatAskOutSchema, KbChatAskSchema
from .sse import PROTOCOL_VERSION

KbChatRouter = APIRouter(route_class=ApiLogRoute, prefix="/chat", tags=["207-AI 对话"])


@KbChatRouter.post(
    "/ask",
    summary="01-发起一轮 AI 问答",
    response_model=ResponseSchema[KbChatAskOutSchema],
)
async def ask_chat_controller(
    data: Annotated[KbChatAskSchema, Body(description="问答条件：会话可留空（首轮自动建），用户 ID 必填")],
) -> JSONResponse:
    result = await KbChatAdminService().ask(data)
    return SuccessResponse(data=result, msg="AI 问答完成")


@KbChatRouter.post(
    "/ask/stream",
    summary="02-发起一轮 AI 问答（SSE 流式）",
    response_model=None,
    responses={
        200: {
            "description": "SSE 帧流：meta → stage → delta* → citations → done（失败为 error 收尾）",
            "content": {SSE_MEDIA_TYPE: {"schema": {"type": "string"}}},
        }
    },
)
async def ask_chat_stream_controller(
    request: Request,
    data: Annotated[KbChatAskSchema, Body(description="问答条件：会话可留空（首轮自动建），用户 ID 必填")],
) -> StreamingResponse:
    """流式出口：校验在返回响应之前完成，因此 400/404 仍是普通 JSON（规范 S5）。"""
    frames = await KbChatAdminService().open_stream(data, is_disconnected=request.is_disconnected)

    async def _sse_frames() -> AsyncIterator[str]:
        async for frame in frames:
            yield format_sse(frame)

    return StreamingResponse(
        _sse_frames(),
        media_type=SSE_MEDIA_TYPE,
        headers={**SSE_HEADERS, "X-Stream-Version": str(PROTOCOL_VERSION)},
    )
