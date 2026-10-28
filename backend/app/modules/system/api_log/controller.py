from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_schema import PageResultSchema, PaginationQueryParam
from app.core.dependencies import db_getter
from app.core.router_class import ApiLogRoute

from .schema import SystemApiLogCleanSchema, SystemApiLogDetailOutSchema, SystemApiLogOutSchema, SystemApiLogQueryParam
from .service import SystemApiLogService

SystemApiLogRouter = APIRouter(route_class=ApiLogRoute, prefix="/api-log", tags=["101-接口审计日志"])


@SystemApiLogRouter.get("/list", summary="01-分页查询接口审计日志", response_model=ResponseSchema[PageResultSchema[SystemApiLogOutSchema]])
async def get_system_api_log_list_controller(
        db: Annotated[AsyncSession, Depends(db_getter)],
        page: Annotated[PaginationQueryParam, Depends()],
        search: Annotated[SystemApiLogQueryParam, Query()],
) -> JSONResponse:
    result_dict = await SystemApiLogService(db).page(
        page_no=page.page_no,
        page_size=page.page_size,
        search=search,
        order_by=page.order_by,
    )
    return SuccessResponse(data=result_dict, msg="查询接口审计日志成功")


@SystemApiLogRouter.get("/detail/{id}", summary="02-查询接口审计日志详情", response_model=ResponseSchema[SystemApiLogDetailOutSchema])
async def get_system_api_log_detail_controller(
        db: Annotated[AsyncSession, Depends(db_getter)],
        id: Annotated[int, Path(description="日志ID", ge=1)],
) -> JSONResponse:
    result_dict = await SystemApiLogService(db).detail(id=id)
    return SuccessResponse(data=result_dict, msg="获取接口审计日志详情成功")


@SystemApiLogRouter.delete("/delete", summary="03-删除接口审计日志", response_model=ResponseSchema[None])
async def delete_system_api_log_controller(
        db: Annotated[AsyncSession, Depends(db_getter)],
        ids: Annotated[list[int], Body(description="ID列表")],
) -> JSONResponse:
    await SystemApiLogService(db).delete(ids=ids)
    return SuccessResponse(msg="删除接口审计日志成功")


@SystemApiLogRouter.post("/clean", summary="04-按保留天数清理接口审计日志", response_model=ResponseSchema[int])
async def clean_system_api_log_controller(
        db: Annotated[AsyncSession, Depends(db_getter)],
        data: Annotated[SystemApiLogCleanSchema, Body(description="清理参数")],
) -> JSONResponse:
    deleted = await SystemApiLogService(db).clean(days=data.days)
    return SuccessResponse(data=deleted, msg=f"已清理 {deleted} 条过期接口审计日志")
