"""GSI 组织架构只读接口（``/gsi/sys/org``）。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_schema import PageResultSchema, PaginationQueryParam
from app.core.dependencies import db_getter
from app.core.router_class import ApiLogRoute

from .schema import SbpSysOrgOutSchema, SbpSysOrgQueryParam
from .service import SbpSysOrgService

SbpSysOrgRouter = APIRouter(
    route_class=ApiLogRoute, prefix="/sys/org", tags=["201-GSI组织架构"]
)


@SbpSysOrgRouter.get(
    "/list",
    summary="01-分页查询GSI组织架构",
    response_model=ResponseSchema[PageResultSchema[SbpSysOrgOutSchema]],
)
async def get_sbp_sys_org_list_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    page: Annotated[PaginationQueryParam, Depends()],
    search: Annotated[SbpSysOrgQueryParam, Query()],
) -> JSONResponse:
    result_dict = await SbpSysOrgService(db).page(
        page_no=page.page_no,
        page_size=page.page_size,
        search=search,
        order_by=page.order_by,
    )
    return SuccessResponse(data=result_dict, msg="查询GSI组织架构成功")


@SbpSysOrgRouter.get(
    "/detail/{id}",
    summary="02-查询GSI组织架构详情",
    response_model=ResponseSchema[SbpSysOrgOutSchema],
)
async def get_sbp_sys_org_detail_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    id: Annotated[int, Path(description="组织ID", ge=1)],
) -> JSONResponse:
    result_dict = await SbpSysOrgService(db).detail(id=id)
    return SuccessResponse(data=result_dict, msg="获取GSI组织架构详情成功")
