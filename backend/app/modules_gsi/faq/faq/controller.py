"""GSI FAQ 主记录只读接口（``/gsi/faq``）。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_schema import PageResultSchema, PaginationQueryParam
from app.core.dependencies import db_getter
from app.core.router_class import ApiLogRoute

from .schema import SbpFaqOutSchema, SbpFaqQueryParam
from .service import SbpFaqService

SbpFaqRouter = APIRouter(route_class=ApiLogRoute, prefix="/faq", tags=["209-FAQ主记录"])


@SbpFaqRouter.get(
    "/list",
    summary="01-分页查询FAQ主记录",
    response_model=ResponseSchema[PageResultSchema[SbpFaqOutSchema]],
)
async def get_sbp_faq_list_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    page: Annotated[PaginationQueryParam, Depends()],
    search: Annotated[SbpFaqQueryParam, Query()],
) -> JSONResponse:
    result_dict = await SbpFaqService(db).page(
        page_no=page.page_no,
        page_size=page.page_size,
        search=search,
        order_by=page.order_by,
    )
    return SuccessResponse(data=result_dict, msg="查询FAQ主记录成功")


@SbpFaqRouter.get(
    "/detail/{id}",
    summary="02-查询FAQ主记录详情",
    response_model=ResponseSchema[SbpFaqOutSchema],
)
async def get_sbp_faq_detail_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    id: Annotated[str, Path(description="FAQ 主键", min_length=1, max_length=64)],
) -> JSONResponse:
    result_dict = await SbpFaqService(db).detail(id=id)
    return SuccessResponse(data=result_dict, msg="获取FAQ主记录详情成功")
