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
    SbpSysAudienceTeamMemberWithTeamOutSchema,
    SbpSysAudienceTeamMemberWithTeamQueryParam,
    SbpUserTeamsOutSchema,
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


@SbpSysAudienceTeamMemberRouter.get(
    "/page-with-team",
    summary="03-分页查询受众团队成员（含团队标识）",
    response_model=ResponseSchema[
        PageResultSchema[SbpSysAudienceTeamMemberWithTeamOutSchema]
    ],
)
async def get_sbp_sys_audience_team_member_page_with_team_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    page: Annotated[PaginationQueryParam, Depends()],
    search: Annotated[SbpSysAudienceTeamMemberWithTeamQueryParam, Query()],
) -> JSONResponse:
    result_dict = await SbpSysAudienceTeamMemberService(db).page_with_team(
        page_no=page.page_no,
        page_size=page.page_size,
        search=search,
    )
    return SuccessResponse(data=result_dict, msg="查询受众团队成员（含团队标识）成功")


@SbpSysAudienceTeamMemberRouter.get(
    "/user-teams",
    summary="04-按用户反查受众团队标识",
    response_model=ResponseSchema[SbpUserTeamsOutSchema],
)
async def get_sbp_sys_user_teams_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    user_id: Annotated[int, Query(description="用户ID（契约签名 user_id）", ge=1)],
) -> JSONResponse:
    result_dict = await SbpSysAudienceTeamMemberService(db).user_teams(user_id=user_id)
    return SuccessResponse(data=result_dict, msg="查询用户受众团队标识成功")
