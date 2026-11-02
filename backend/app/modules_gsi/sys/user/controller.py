"""GSI 用户有效性只读接口（``/gsi/sys/user``）。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_schema import PageResultSchema, PaginationQueryParam
from app.core.dependencies import db_getter
from app.core.router_class import ApiLogRoute

from .schema import SbpSysUserOutSchema, SbpSysUserQueryParam
from .service import SbpSysUserService

SbpSysUserRouter = APIRouter(
    route_class=ApiLogRoute, prefix="/sys/user", tags=["205-GSI用户有效性"]
)


@SbpSysUserRouter.get(
    "/list",
    summary="01-分页查询GSI用户有效性",
    response_model=ResponseSchema[PageResultSchema[SbpSysUserOutSchema]],
)
async def get_sbp_sys_user_list_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    page: Annotated[PaginationQueryParam, Depends()],
    search: Annotated[SbpSysUserQueryParam, Query()],
) -> JSONResponse:
    result_dict = await SbpSysUserService(db).page(
        page_no=page.page_no,
        page_size=page.page_size,
        search=search,
        order_by=page.order_by,
    )
    return SuccessResponse(data=result_dict, msg="查询GSI用户有效性成功")


@SbpSysUserRouter.get(
    "/detail/{id}",
    summary="02-查询GSI用户有效性详情",
    response_model=ResponseSchema[SbpSysUserOutSchema],
)
async def get_sbp_sys_user_detail_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    id: Annotated[int, Path(description="用户ID", ge=1)],
) -> JSONResponse:
    result_dict = await SbpSysUserService(db).detail(id=id)
    return SuccessResponse(data=result_dict, msg="获取GSI用户有效性详情成功")
