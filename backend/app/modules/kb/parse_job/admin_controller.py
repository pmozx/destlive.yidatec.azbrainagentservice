"""解析任务管理 Controller：只读列表 + 详情（后台解析状态机由契约链路驱动）。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_schema import PageResultSchema, PaginationQueryParam
from app.core.dependencies import db_getter
from app.core.router_class import ApiLogRoute

from .admin_service import KbParseJobAdminService
from .schema import KbParseJobDetailOutSchema, KbParseJobOutSchema, KbParseJobQueryParam

KbParseJobRouter = APIRouter(route_class=ApiLogRoute, prefix="/parse-jobs", tags=["202-解析任务"])


@KbParseJobRouter.get(
    "/list",
    summary="01-分页查询解析任务",
    response_model=ResponseSchema[PageResultSchema[KbParseJobOutSchema]],
)
async def get_parse_job_list_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    page: Annotated[PaginationQueryParam, Depends()],
    search: Annotated[KbParseJobQueryParam, Query()],
) -> JSONResponse:
    result = await KbParseJobAdminService(db).page(
        page_no=page.page_no,
        page_size=page.page_size,
        search=search,
        order_by=page.order_by,
    )
    return SuccessResponse(data=result, msg="查询解析任务成功")


@KbParseJobRouter.get(
    "/detail/{job_id}",
    summary="02-查询解析任务详情",
    response_model=ResponseSchema[KbParseJobDetailOutSchema],
)
async def get_parse_job_detail_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    job_id: Annotated[str, Path(description="契约 job_id", min_length=1, max_length=64)],
) -> JSONResponse:
    result = await KbParseJobAdminService(db).detail(job_id)
    return SuccessResponse(data=result, msg="获取解析任务详情成功")
