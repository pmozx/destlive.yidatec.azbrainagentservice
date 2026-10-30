"""集成-AI 问答接口（/integration-chat/*）。

``/stream`` 是唯一的长连接入口：一次性入参（问题 + 范围），持续回帧，
因此走 ``StreamResponse``（text/event-stream），不套统一 JSON 包装。
"""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import ResponseSchema, StreamResponse, SuccessResponse
from app.core.dependencies import db_getter
from app.core.router_class import ApiLogRoute
from app.utils.sse import SSE_HEADERS, SSE_MEDIA_TYPE

from .schema import ChatStreamInSchema, FeedbackInSchema
from .service import IntegrationChatService, stream_chat

IntegrationChatRouter = APIRouter(route_class=ApiLogRoute, tags=["210-集成问答-问答会话"])


@IntegrationChatRouter.post("/stream", summary="01-流式问答（SSE）")
async def chat_stream_controller(
        request: Request,
        data: Annotated[ChatStreamInSchema, Body(description="流式问答参数")],
) -> StreamResponse:
    # 不注入 db：SSE 生命周期长于一次请求，检索会话由服务层自行开关（见 service.stream_chat）
    return StreamResponse(
        data=stream_chat(data=data, request=request),
        media_type=SSE_MEDIA_TYPE,
        headers={**SSE_HEADERS, "Content-Type": f"{SSE_MEDIA_TYPE}; charset=utf-8"},
    )


@IntegrationChatRouter.get("/scope", summary="02-查询问答范围（启用空间清单）", response_model=ResponseSchema[list[dict]])
async def get_chat_scope_controller(
        db: Annotated[AsyncSession, Depends(db_getter)],
) -> JSONResponse:
    result_dict = await IntegrationChatService(db).scope_options()
    return SuccessResponse(data=result_dict, msg="查询问答范围成功")


@IntegrationChatRouter.put("/message/feedback/{id}", summary="03-提交答案反馈", response_model=ResponseSchema[None])
async def set_message_feedback_controller(
        db: Annotated[AsyncSession, Depends(db_getter)],
        id: Annotated[int, Path(description="消息ID", ge=1)],
        data: Annotated[FeedbackInSchema, Body(description="反馈参数")],
) -> JSONResponse:
    await IntegrationChatService(db).set_feedback(message_id=id, feedback=data.feedback)
    return SuccessResponse(msg="提交答案反馈成功")
