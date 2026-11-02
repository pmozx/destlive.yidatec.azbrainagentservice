"""重复分组治理 Controller：列表 + 详情 + 标记处理。"""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_schema import PageResultSchema, PaginationQueryParam
from app.core.dependencies import db_getter
from app.core.router_class import ApiLogRoute

from .schema import KbDupGroupDetailOutSchema, KbDupGroupOutSchema, KbDupGroupQueryParam, KbDupGroupStatusSchema
from .service import KbDupGroupService

KbDupGroupRouter = APIRouter(route_class=ApiLogRoute, prefix="/dup-groups", tags=["203-重复治理"])


@KbDupGroupRouter.get(
    "/list",
    summary="01-分页查询重复分组",
    response_model=ResponseSchema[PageResultSchema[KbDupGroupOutSchema]],
)
async def get_dup_group_list_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    page: Annotated[PaginationQueryParam, Depends()],
    search: Annotated[KbDupGroupQueryParam, Query()],
) -> JSONResponse:
    result = await KbDupGroupService(db).page(
        page_no=page.page_no,
        page_size=page.page_size,
        search=search,
        order_by=page.order_by,
    )
    return SuccessResponse(data=result, msg="查询重复分组成功")


@KbDupGroupRouter.get(
    "/detail/{group_id}",
    summary="02-查询重复分组详情",
    response_model=ResponseSchema[KbDupGroupDetailOutSchema],
)
async def get_dup_group_detail_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    group_id: Annotated[int, Path(description="分组主键", ge=1)],
) -> JSONResponse:
    result = await KbDupGroupService(db).detail(group_id)
    return SuccessResponse(data=result, msg="获取重复分组详情成功")


@KbDupGroupRouter.patch(
    "/status/{group_id}",
    summary="03-标记重复分组处理结果",
    response_model=ResponseSchema[KbDupGroupDetailOutSchema],
)
async def update_dup_group_status_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    group_id: Annotated[int, Path(description="分组主键", ge=1)],
    data: Annotated[KbDupGroupStatusSchema, Body(description="merged/dismissed")],
) -> JSONResponse:
    result = await KbDupGroupService(db).update_status(group_id, data)
    return SuccessResponse(data=result, msg="标记重复分组处理结果成功")
