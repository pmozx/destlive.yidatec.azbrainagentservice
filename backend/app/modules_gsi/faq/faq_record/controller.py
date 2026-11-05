"""GSI FAQ 审批快照只读接口（``/gsi/faq/record``）。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_schema import PageResultSchema, PaginationQueryParam
from app.core.dependencies import db_getter
from app.core.router_class import ApiLogRoute

from .schema import (
    SbpFaqRecordDetailOutSchema,
    SbpFaqRecordOutSchema,
    SbpFaqRecordQueryParam,
)
from .service import SbpFaqRecordService

SbpFaqRecordRouter = APIRouter(
    route_class=ApiLogRoute, prefix="/faq/record", tags=["211-FAQ生效快照"]
)


@SbpFaqRecordRouter.get(
    "/list",
    summary="01-分页查询FAQ审批快照",
    response_model=ResponseSchema[PageResultSchema[SbpFaqRecordOutSchema]],
)
async def get_sbp_faq_record_list_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    page: Annotated[PaginationQueryParam, Depends()],
    search: Annotated[SbpFaqRecordQueryParam, Query()],
) -> JSONResponse:
    result_dict = await SbpFaqRecordService(db).page(
        page_no=page.page_no,
        page_size=page.page_size,
        search=search,
        order_by=page.order_by,
    )
    return SuccessResponse(data=result_dict, msg="查询FAQ审批快照成功")


@SbpFaqRecordRouter.get(
    "/detail/{id}",
    summary="02-查询FAQ审批快照详情",
    response_model=ResponseSchema[SbpFaqRecordDetailOutSchema],
)
async def get_sbp_faq_record_detail_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    id: Annotated[str, Path(description="快照主键", min_length=1, max_length=64)],
) -> JSONResponse:
    result_dict = await SbpFaqRecordService(db).detail(id=id)
    return SuccessResponse(data=result_dict, msg="获取FAQ审批快照详情成功")
