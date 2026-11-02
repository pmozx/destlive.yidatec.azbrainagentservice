"""GSI 受众团队成员只读接口（``/gsi/sys/audience-team-member``）。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_schema import PageResultSchema, PaginationQueryParam
from app.core.dependencies import db_getter
from app.core.router_class import ApiLogRoute

from .schema import (
    SbpSysAudienceTeamMemberOutSchema,
    SbpSysAudienceTeamMemberQueryParam,
)
from .service import SbpSysAudienceTeamMemberService

SbpSysAudienceTeamMemberRouter = APIRouter(
    route_class=ApiLogRoute,
    prefix="/sys/audience-team-member",
    tags=["204-GSI受众团队成员"],
)


@SbpSysAudienceTeamMemberRouter.get(
    "/list",
    summary="01-分页查询GSI受众团队成员",
    response_model=ResponseSchema[PageResultSchema[SbpSysAudienceTeamMemberOutSchema]],
)
async def get_sbp_sys_audience_team_member_list_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    page: Annotated[PaginationQueryParam, Depends()],
    search: Annotated[SbpSysAudienceTeamMemberQueryParam, Query()],
) -> JSONResponse:
    result_dict = await SbpSysAudienceTeamMemberService(db).page(
        page_no=page.page_no,
        page_size=page.page_size,
        search=search,
        order_by=page.order_by,
    )
    return SuccessResponse(data=result_dict, msg="查询GSI受众团队成员成功")
