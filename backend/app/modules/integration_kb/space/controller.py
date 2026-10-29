"""知识空间接口（/integration-kb/space/*）。"""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_schema import PageResultSchema, PaginationQueryParam
from app.core.dependencies import db_getter
from app.core.router_class import ApiLogRoute

from .schema import SpaceCreateSchema, SpaceOutSchema, SpaceQueryParam, SpaceUpdateSchema
from .service import IntegrationKbSpaceService

IntegrationKbSpaceRouter = APIRouter(route_class=ApiLogRoute, prefix="/space", tags=["201-知识库-知识空间"])


@IntegrationKbSpaceRouter.get("/list", summary="01-分页查询知识空间", response_model=ResponseSchema[PageResultSchema[SpaceOutSchema]])
async def get_space_list_controller(
        db: Annotated[AsyncSession, Depends(db_getter)],
        page: Annotated[PaginationQueryParam, Depends()],
        search: Annotated[SpaceQueryParam, Query()],
) -> JSONResponse:
    result_dict = await IntegrationKbSpaceService(db).page(
        page_no=page.page_no,
        page_size=page.page_size,
        search=search,
        order_by=page.order_by,
    )
    return SuccessResponse(data=result_dict, msg="查询知识空间成功")


@IntegrationKbSpaceRouter.get("/options", summary="02-查询知识空间下拉选项", response_model=ResponseSchema[list[SpaceOutSchema]])
async def get_space_options_controller(
        db: Annotated[AsyncSession, Depends(db_getter)],
) -> JSONResponse:
    result_dict = await IntegrationKbSpaceService(db).options()
    return SuccessResponse(data=result_dict, msg="查询知识空间选项成功")


@IntegrationKbSpaceRouter.get("/detail/{id}", summary="03-查询知识空间详情", response_model=ResponseSchema[SpaceOutSchema])
async def get_space_detail_controller(
        db: Annotated[AsyncSession, Depends(db_getter)],
        id: Annotated[int, Path(description="空间ID", ge=1)],
) -> JSONResponse:
    result_dict = await IntegrationKbSpaceService(db).detail(id=id)
    return SuccessResponse(data=result_dict, msg="获取知识空间详情成功")


@IntegrationKbSpaceRouter.post(
    "/create",
    status_code=status.HTTP_201_CREATED,
    summary="04-创建知识空间",
    response_model=ResponseSchema[SpaceOutSchema],
)
async def create_space_controller(
        db: Annotated[AsyncSession, Depends(db_getter)],
        data: Annotated[SpaceCreateSchema, Body(description="知识空间创建参数")],
) -> JSONResponse:
    result_dict = await IntegrationKbSpaceService(db).create(data=data)
    return SuccessResponse(data=result_dict, msg="创建知识空间成功")


@IntegrationKbSpaceRouter.put("/update/{id}", summary="05-修改知识空间", response_model=ResponseSchema[SpaceOutSchema])
async def update_space_controller(
        db: Annotated[AsyncSession, Depends(db_getter)],
        id: Annotated[int, Path(description="空间ID", ge=1)],
        data: Annotated[SpaceUpdateSchema, Body(description="知识空间修改参数")],
) -> JSONResponse:
    result_dict = await IntegrationKbSpaceService(db).update(id=id, data=data)
    return SuccessResponse(data=result_dict, msg="修改知识空间成功")


@IntegrationKbSpaceRouter.delete("/delete", summary="06-删除知识空间", response_model=ResponseSchema[None])
async def delete_space_controller(
        db: Annotated[AsyncSession, Depends(db_getter)],
        ids: Annotated[list[int], Body(description="空间ID列表")],
) -> JSONResponse:
    await IntegrationKbSpaceService(db).delete(ids=ids)
    return SuccessResponse(msg="删除知识空间成功（已级联清理分类、文档、分片与对象存储）")
