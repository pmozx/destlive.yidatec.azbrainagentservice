"""GSI 职级只读接口（``/gsi/sys/rank``）。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_schema import PageResultSchema, PaginationQueryParam
from app.core.dependencies import db_getter
from app.core.router_class import ApiLogRoute

from .schema import SbpSysRankOutSchema, SbpSysRankQueryParam
from .service import SbpSysRankService

SbpSysRankRouter = APIRouter(
    route_class=ApiLogRoute, prefix="/sys/rank", tags=["212-GSI职级"]
)


@SbpSysRankRouter.get(
    "/list",
    summary="01-分页查询GSI职级",
    response_model=ResponseSchema[PageResultSchema[SbpSysRankOutSchema]],
)
async def get_sbp_sys_rank_list_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    page: Annotated[PaginationQueryParam, Depends()],
    search: Annotated[SbpSysRankQueryParam, Query()],
) -> JSONResponse:
    result_dict = await SbpSysRankService(db).page(
        page_no=page.page_no,
        page_size=page.page_size,
        search=search,
        order_by=page.order_by,
    )
    return SuccessResponse(data=result_dict, msg="查询GSI职级成功")


@SbpSysRankRouter.get(
    "/detail/{id}",
    summary="02-查询GSI职级详情",
    response_model=ResponseSchema[SbpSysRankOutSchema],
)
async def get_sbp_sys_rank_detail_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    id: Annotated[int, Path(description="职级ID", ge=1)],
) -> JSONResponse:
    result_dict = await SbpSysRankService(db).detail(id=id)
    return SuccessResponse(data=result_dict, msg="获取GSI职级详情成功")
