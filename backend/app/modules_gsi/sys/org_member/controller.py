"""GSI 组织成员只读接口（``/gsi/sys/org-member``）。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_schema import PageResultSchema, PaginationQueryParam
from app.core.dependencies import db_getter
from app.core.router_class import ApiLogRoute

from .schema import SbpSysOrgMemberOutSchema, SbpSysOrgMemberQueryParam
from .service import SbpSysOrgMemberService

SbpSysOrgMemberRouter = APIRouter(
    route_class=ApiLogRoute, prefix="/sys/org-member", tags=["202-GSI组织成员"]
)


@SbpSysOrgMemberRouter.get(
    "/list",
    summary="01-分页查询GSI组织成员",
    response_model=ResponseSchema[PageResultSchema[SbpSysOrgMemberOutSchema]],
)
async def get_sbp_sys_org_member_list_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    page: Annotated[PaginationQueryParam, Depends()],
    search: Annotated[SbpSysOrgMemberQueryParam, Query()],
) -> JSONResponse:
    result_dict = await SbpSysOrgMemberService(db).page(
        page_no=page.page_no,
        page_size=page.page_size,
        search=search,
        order_by=page.order_by,
    )
    return SuccessResponse(data=result_dict, msg="查询GSI组织成员成功")
