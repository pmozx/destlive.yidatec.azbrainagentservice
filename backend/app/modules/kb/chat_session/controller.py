"""问答会话 Controller：管理端维护页（列表 / 详情 / 改标题 / 清空 / 删除）。

路由前缀 ``/kb/chat-session``（域前缀由 ``app/api/v1/routers.py`` 挂载），整组接口挂
``ApiLogRoute`` 自动留痕。管理端不校验归属——管理员需要按用户排查会话，归属隔离在
``/v1/chat/sessions`` 那一层；领域异常在这里统一翻成 HTTP 404。
"""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_schema import PageResultSchema, PaginationQueryParam
from app.core.dependencies import db_getter
from app.core.exceptions import CustomException
from app.core.router_class import ApiLogRoute

from .schema import (
    KbChatSessionDetailOutSchema,
    KbChatSessionOutSchema,
    KbChatSessionQueryParam,
    KbChatSessionUpdateSchema,
)
from .service import ChatSessionNotFoundError, KbChatSessionService

KbChatSessionRouter = APIRouter(route_class=ApiLogRoute, prefix="/chat-session", tags=["205-会话管理"])


@KbChatSessionRouter.get(
    "/list",
    summary="01-分页查询问答会话",
    response_model=ResponseSchema[PageResultSchema[KbChatSessionOutSchema]],
)
async def get_chat_session_list_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    page: Annotated[PaginationQueryParam, Depends()],
    search: Annotated[KbChatSessionQueryParam, Query()],
) -> JSONResponse:
    result = await KbChatSessionService(db).page(
        page_no=page.page_no,
        page_size=page.page_size,
        search=search,
        order_by=page.order_by,
    )
    return SuccessResponse(data=result, msg="查询问答会话成功")


@KbChatSessionRouter.get(
    "/detail/{session_id}",
    summary="02-查询问答会话详情",
    response_model=ResponseSchema[KbChatSessionDetailOutSchema],
)
async def get_chat_session_detail_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    session_id: Annotated[str, Path(description="会话 ID", min_length=1, max_length=64)],
) -> JSONResponse:
    try:
        result = await KbChatSessionService(db).detail(session_id)
    except ChatSessionNotFoundError as exc:
        raise CustomException(msg=str(exc), status_code=status.HTTP_404_NOT_FOUND) from exc
    return SuccessResponse(data=result, msg="获取问答会话详情成功")


@KbChatSessionRouter.put(
    "/rename/{session_id}",
    summary="03-修改会话标题",
    response_model=ResponseSchema[KbChatSessionOutSchema],
)
async def rename_chat_session_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    session_id: Annotated[str, Path(description="会话 ID", min_length=1, max_length=64)],
    data: Annotated[KbChatSessionUpdateSchema, Body(description="新的会话标题")],
) -> JSONResponse:
    try:
        result = await KbChatSessionService(db).update(session_id, data)
    except ChatSessionNotFoundError as exc:
        raise CustomException(msg=str(exc), status_code=status.HTTP_404_NOT_FOUND) from exc
    return SuccessResponse(data=result, msg="修改会话标题成功")


@KbChatSessionRouter.put(
    "/clear/{session_id}",
    summary="04-清空会话历史",
    response_model=ResponseSchema[KbChatSessionOutSchema],
)
async def clear_chat_session_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    session_id: Annotated[str, Path(description="会话 ID", min_length=1, max_length=64)],
) -> JSONResponse:
    try:
        result = await KbChatSessionService(db).clear(session_id)
    except ChatSessionNotFoundError as exc:
        raise CustomException(msg=str(exc), status_code=status.HTTP_404_NOT_FOUND) from exc
    return SuccessResponse(data=result, msg="清空会话历史成功")


@KbChatSessionRouter.delete(
    "/delete/{session_id}",
    summary="05-删除问答会话",
    response_model=ResponseSchema[None],
)
async def delete_chat_session_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    session_id: Annotated[str, Path(description="会话 ID", min_length=1, max_length=64)],
) -> JSONResponse:
    try:
        await KbChatSessionService(db).delete(session_id)
    except ChatSessionNotFoundError as exc:
        raise CustomException(msg=str(exc), status_code=status.HTTP_404_NOT_FOUND) from exc
    return SuccessResponse(msg="删除问答会话成功")
