"""问答会话接口（/integration-chat/session/*）。"""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_schema import PageResultSchema, PaginationQueryParam
from app.core.dependencies import db_getter
from app.core.router_class import ApiLogRoute

from ..schema import SessionCreateSchema, SessionDetailOutSchema, SessionOutSchema, SessionQueryParam, SessionUpdateSchema
from .service import IntegrationChatSessionService

IntegrationChatSessionRouter = APIRouter(
    route_class=ApiLogRoute, prefix="/session", tags=["211-集成问答-会话管理"]
)


@IntegrationChatSessionRouter.get(
    "/list",
    summary="01-分页查询问答会话",
    response_model=ResponseSchema[PageResultSchema[SessionOutSchema]],
)
async def get_session_list_controller(
        db: Annotated[AsyncSession, Depends(db_getter)],
        page: Annotated[PaginationQueryParam, Depends()],
        search: Annotated[SessionQueryParam, Query()],
) -> JSONResponse:
    result_dict = await IntegrationChatSessionService(db).page(
        page_no=page.page_no,
        page_size=page.page_size,
        search=search,
        order_by=page.order_by,
    )
    return SuccessResponse(data=result_dict, msg="查询问答会话成功")


@IntegrationChatSessionRouter.get(
    "/detail/{id}",
    summary="02-查询会话详情（含消息明细）",
    response_model=ResponseSchema[SessionDetailOutSchema],
)
async def get_session_detail_controller(
        db: Annotated[AsyncSession, Depends(db_getter)],
        id: Annotated[int, Path(description="会话ID", ge=1)],
) -> JSONResponse:
    result_dict = await IntegrationChatSessionService(db).detail(id=id)
    return SuccessResponse(data=result_dict, msg="查询会话详情成功")


@IntegrationChatSessionRouter.post(
    "/create",
    status_code=status.HTTP_201_CREATED,
    summary="03-创建问答会话",
    response_model=ResponseSchema[SessionOutSchema],
)
async def create_session_controller(
        db: Annotated[AsyncSession, Depends(db_getter)],
        data: Annotated[SessionCreateSchema, Body(description="会话创建参数")],
) -> JSONResponse:
    result_dict = await IntegrationChatSessionService(db).create(data=data)
    return SuccessResponse(data=result_dict, msg="创建问答会话成功")


@IntegrationChatSessionRouter.put(
    "/update/{id}",
    summary="04-重命名问答会话",
    response_model=ResponseSchema[SessionOutSchema],
)
async def update_session_controller(
        db: Annotated[AsyncSession, Depends(db_getter)],
        id: Annotated[int, Path(description="会话ID", ge=1)],
        data: Annotated[SessionUpdateSchema, Body(description="会话修改参数")],
) -> JSONResponse:
    result_dict = await IntegrationChatSessionService(db).update(id=id, data=data)
    return SuccessResponse(data=result_dict, msg="重命名问答会话成功")


@IntegrationChatSessionRouter.delete(
    "/delete",
    summary="05-删除问答会话（消息一并清理）",
    response_model=ResponseSchema[None],
)
async def delete_session_controller(
        db: Annotated[AsyncSession, Depends(db_getter)],
        ids: Annotated[list[int], Body(description="会话ID列表")],
) -> JSONResponse:
    await IntegrationChatSessionService(db).delete(ids=ids)
    return SuccessResponse(msg="删除问答会话成功")
