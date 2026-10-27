from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_schema import PageResultSchema, PaginationQueryParam
from app.core.dependencies import db_getter
from app.core.router_class import OperationLogRoute

from .schema import OperationLogCleanSchema, OperationLogDetailOutSchema, OperationLogOutSchema, OperationLogQueryParam
from .service import OperationLogService

LogRouter = APIRouter(route_class=OperationLogRoute, prefix="/log/operation", tags=["101-操作日志"])


@LogRouter.get("/list", summary="01-分页查询操作日志", response_model=ResponseSchema[PageResultSchema[OperationLogOutSchema]])
async def get_operation_log_list_controller(
        db: Annotated[AsyncSession, Depends(db_getter)],
        page: Annotated[PaginationQueryParam, Depends()],
        search: Annotated[OperationLogQueryParam, Query()],
) -> JSONResponse:
    result_dict = await OperationLogService(db).page(
        page_no=page.page_no,
        page_size=page.page_size,
        search=search,
        order_by=page.order_by,
    )
    return SuccessResponse(data=result_dict, msg="查询操作日志成功")


@LogRouter.get("/detail/{id}", summary="02-查询操作日志详情", response_model=ResponseSchema[OperationLogDetailOutSchema])
async def get_operation_log_detail_controller(
        db: Annotated[AsyncSession, Depends(db_getter)],
        id: Annotated[int, Path(description="日志ID", ge=1)],
) -> JSONResponse:
    result_dict = await OperationLogService(db).detail(id=id)
    return SuccessResponse(data=result_dict, msg="获取操作日志详情成功")


@LogRouter.delete("/delete", summary="03-删除操作日志", response_model=ResponseSchema[None])
async def delete_operation_log_controller(
        db: Annotated[AsyncSession, Depends(db_getter)],
        ids: Annotated[list[int], Body(description="ID列表")],
) -> JSONResponse:
    await OperationLogService(db).delete(ids=ids)
    return SuccessResponse(msg="删除操作日志成功")


@LogRouter.post("/clean", summary="04-按保留天数清理操作日志", response_model=ResponseSchema[int])
async def clean_operation_log_controller(
        db: Annotated[AsyncSession, Depends(db_getter)],
        data: Annotated[OperationLogCleanSchema, Body(description="清理参数")],
) -> JSONResponse:
    deleted = await OperationLogService(db).clean(days=data.days)
    return SuccessResponse(data=deleted, msg=f"已清理 {deleted} 条过期操作日志")
