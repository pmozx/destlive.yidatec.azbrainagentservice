"""GSI 受众团队只读接口（``/gsi/sys/audience-team``）。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_schema import PageResultSchema, PaginationQueryParam
from app.core.dependencies import db_getter
from app.core.router_class import ApiLogRoute

from .schema import SbpSysAudienceTeamOutSchema, SbpSysAudienceTeamQueryParam
from .service import SbpSysAudienceTeamService

SbpSysAudienceTeamRouter = APIRouter(
    route_class=ApiLogRoute, prefix="/sys/audience-team", tags=["203-GSI受众团队"]
)


@SbpSysAudienceTeamRouter.get(
    "/list",
    summary="01-分页查询GSI受众团队",
    response_model=ResponseSchema[PageResultSchema[SbpSysAudienceTeamOutSchema]],
)
async def get_sbp_sys_audience_team_list_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    page: Annotated[PaginationQueryParam, Depends()],
    search: Annotated[SbpSysAudienceTeamQueryParam, Query()],
) -> JSONResponse:
    result_dict = await SbpSysAudienceTeamService(db).page(
        page_no=page.page_no,
        page_size=page.page_size,
        search=search,
        order_by=page.order_by,
    )
    return SuccessResponse(data=result_dict, msg="查询GSI受众团队成功")


@SbpSysAudienceTeamRouter.get(
    "/detail/{id}",
    summary="02-查询GSI受众团队详情",
    response_model=ResponseSchema[SbpSysAudienceTeamOutSchema],
)
async def get_sbp_sys_audience_team_detail_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    id: Annotated[int, Path(description="受众团队ID", ge=1)],
) -> JSONResponse:
    result_dict = await SbpSysAudienceTeamService(db).detail(id=id)
    return SuccessResponse(data=result_dict, msg="获取GSI受众团队详情成功")
