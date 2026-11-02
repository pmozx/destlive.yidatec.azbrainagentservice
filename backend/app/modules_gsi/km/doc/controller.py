"""GSI 知识文档只读接口（``/gsi/km/doc``）。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_schema import PageResultSchema, PaginationQueryParam
from app.core.dependencies import db_getter
from app.core.router_class import ApiLogRoute

from .schema import SbpKmDocOutSchema, SbpKmDocQueryParam
from .service import SbpKmDocService

SbpKmDocRouter = APIRouter(
    route_class=ApiLogRoute, prefix="/km/doc", tags=["206-GSI知识文档"]
)


@SbpKmDocRouter.get(
    "/list",
    summary="01-分页查询GSI知识文档",
    response_model=ResponseSchema[PageResultSchema[SbpKmDocOutSchema]],
)
async def get_sbp_km_doc_list_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    page: Annotated[PaginationQueryParam, Depends()],
    search: Annotated[SbpKmDocQueryParam, Query()],
) -> JSONResponse:
    result_dict = await SbpKmDocService(db).page(
        page_no=page.page_no,
        page_size=page.page_size,
        search=search,
        order_by=page.order_by,
    )
    return SuccessResponse(data=result_dict, msg="查询GSI知识文档成功")


@SbpKmDocRouter.get(
    "/detail/{id}",
    summary="02-查询GSI知识文档详情",
    response_model=ResponseSchema[SbpKmDocOutSchema],
)
async def get_sbp_km_doc_detail_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    id: Annotated[str, Path(description="知识主键", min_length=1, max_length=64)],
) -> JSONResponse:
    result_dict = await SbpKmDocService(db).detail(id=id)
    return SuccessResponse(data=result_dict, msg="获取GSI知识文档详情成功")
