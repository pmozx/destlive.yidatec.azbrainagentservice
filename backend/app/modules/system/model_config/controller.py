"""模型配置管理端接口（系统管理 → 模型配置）。"""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_schema import PageResultSchema, PaginationQueryParam
from app.core.dependencies import db_getter
from app.core.router_class import ApiLogRoute

from .schema import (
    SystemModelConfigCreateSchema,
    SystemModelConfigOutSchema,
    SystemModelConfigQueryParam,
    SystemModelConfigTestOutSchema,
    SystemModelConfigUpdateSchema,
)
from .service import SystemModelConfigService

SystemModelConfigRouter = APIRouter(route_class=ApiLogRoute, prefix="/model-config", tags=["104-模型配置"])


@SystemModelConfigRouter.get(
    "/list",
    summary="01-分页查询模型配置",
    response_model=ResponseSchema[PageResultSchema[SystemModelConfigOutSchema]],
)
async def get_system_model_config_list_controller(
        db: Annotated[AsyncSession, Depends(db_getter)],
        page: Annotated[PaginationQueryParam, Depends()],
        search: Annotated[SystemModelConfigQueryParam, Query()],
) -> JSONResponse:
    result_dict = await SystemModelConfigService(db).page(
        page_no=page.page_no,
        page_size=page.page_size,
        search=search,
        order_by=page.order_by,
    )
    return SuccessResponse(data=result_dict, msg="查询模型配置成功")


@SystemModelConfigRouter.get(
    "/detail/{id}",
    summary="02-查询模型配置详情",
    response_model=ResponseSchema[SystemModelConfigOutSchema],
)
async def get_system_model_config_detail_controller(
        db: Annotated[AsyncSession, Depends(db_getter)],
        id: Annotated[int, Path(description="模型配置ID", ge=1)],
) -> JSONResponse:
    result_dict = await SystemModelConfigService(db).detail(id=id)
    return SuccessResponse(data=result_dict, msg="获取模型配置详情成功")


@SystemModelConfigRouter.post(
    "/create",
    summary="03-新增模型配置",
    response_model=ResponseSchema[SystemModelConfigOutSchema],
)
async def create_system_model_config_controller(
        db: Annotated[AsyncSession, Depends(db_getter)],
        data: Annotated[SystemModelConfigCreateSchema, Body(description="模型配置")],
) -> JSONResponse:
    result_dict = await SystemModelConfigService(db).create(data=data)
    return SuccessResponse(data=result_dict, msg="新增模型配置成功")


@SystemModelConfigRouter.put(
    "/update/{id}",
    summary="04-更新模型配置",
    response_model=ResponseSchema[SystemModelConfigOutSchema],
)
async def update_system_model_config_controller(
        db: Annotated[AsyncSession, Depends(db_getter)],
        id: Annotated[int, Path(description="模型配置ID", ge=1)],
        data: Annotated[SystemModelConfigUpdateSchema, Body(description="模型配置（只传要改的字段）")],
) -> JSONResponse:
    result_dict = await SystemModelConfigService(db).update(id=id, data=data)
    return SuccessResponse(data=result_dict, msg="更新模型配置成功")


@SystemModelConfigRouter.delete("/delete", summary="05-删除模型配置", response_model=ResponseSchema[None])
async def delete_system_model_config_controller(
        db: Annotated[AsyncSession, Depends(db_getter)],
        ids: Annotated[list[int], Body(description="ID列表")],
) -> JSONResponse:
    await SystemModelConfigService(db).delete(ids=ids)
    return SuccessResponse(msg="删除模型配置成功")


@SystemModelConfigRouter.post(
    "/test/{id}",
    summary="06-测试模型连通性",
    response_model=ResponseSchema[SystemModelConfigTestOutSchema],
)
async def test_system_model_config_controller(
        db: Annotated[AsyncSession, Depends(db_getter)],
        id: Annotated[int, Path(description="模型配置ID", ge=1)],
) -> JSONResponse:
    result_dict = await SystemModelConfigService(db).test_connection(id=id)
    return SuccessResponse(data=result_dict, msg="已发起模型连通性测试")
