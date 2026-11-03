"""会话消息 Controller：管理端只读（按会话看历史 / 看单条引用留痕）。

路由前缀 ``/kb/chat-message``，整组接口挂 ``ApiLogRoute`` 自动留痕。写入只发生在
``/v1/chat/completions``，因此这里没有任何写接口。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_schema import PageResultSchema, PaginationQueryParam
from app.core.dependencies import db_getter
from app.core.router_class import ApiLogRoute

from .schema import KbChatMessageOutSchema, KbChatMessageQueryParam
from .service import KbChatMessageService

KbChatMessageRouter = APIRouter(route_class=ApiLogRoute, prefix="/chat-message", tags=["206-会话消息"])


@KbChatMessageRouter.get(
    "/list",
    summary="01-分页查询会话消息",
    response_model=ResponseSchema[PageResultSchema[KbChatMessageOutSchema]],
)
async def get_chat_message_list_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    page: Annotated[PaginationQueryParam, Depends()],
    search: Annotated[KbChatMessageQueryParam, Query()],
) -> JSONResponse:
    result = await KbChatMessageService(db).page(
        page_no=page.page_no,
        page_size=page.page_size,
        search=search,
        order_by=page.order_by,
    )
    return SuccessResponse(data=result, msg="查询会话消息成功")


@KbChatMessageRouter.get(
    "/detail/{message_id}",
    summary="02-查询会话消息详情",
    response_model=ResponseSchema[KbChatMessageOutSchema],
)
async def get_chat_message_detail_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    message_id: Annotated[str, Path(description="消息 ID", min_length=1, max_length=64)],
) -> JSONResponse:
    result = await KbChatMessageService(db).detail(message_id)
    return SuccessResponse(data=result, msg="获取会话消息详情成功")
