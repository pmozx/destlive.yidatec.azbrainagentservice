"""GSI 知识生效快照只读接口（``/gsi/km/doc-record``）。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_schema import PageResultSchema, PaginationQueryParam
from app.core.dependencies import db_getter
from app.core.router_class import ApiLogRoute

from .schema import (
    SbpKmDocRecordDetailOutSchema,
    SbpKmDocRecordOutSchema,
    SbpKmDocRecordQueryParam,
)
from .service import SbpKmDocRecordService

SbpKmDocRecordRouter = APIRouter(
    route_class=ApiLogRoute, prefix="/km/doc-record", tags=["208-GSI知识生效快照"]
)


@SbpKmDocRecordRouter.get(
    "/list",
    summary="01-分页查询GSI知识生效快照",
    response_model=ResponseSchema[PageResultSchema[SbpKmDocRecordOutSchema]],
)
async def get_sbp_km_doc_record_list_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    page: Annotated[PaginationQueryParam, Depends()],
    search: Annotated[SbpKmDocRecordQueryParam, Query()],
) -> JSONResponse:
    result_dict = await SbpKmDocRecordService(db).page(
        page_no=page.page_no,
        page_size=page.page_size,
        search=search,
        order_by=page.order_by,
    )
    return SuccessResponse(data=result_dict, msg="查询GSI知识生效快照成功")


@SbpKmDocRecordRouter.get(
    "/detail/{id}",
    summary="02-查询GSI知识生效快照详情",
    response_model=ResponseSchema[SbpKmDocRecordDetailOutSchema],
)
async def get_sbp_km_doc_record_detail_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    id: Annotated[str, Path(description="快照主键", min_length=1, max_length=64)],
) -> JSONResponse:
    result_dict = await SbpKmDocRecordService(db).detail(id=id)
    return SuccessResponse(data=result_dict, msg="获取GSI知识生效快照详情成功")
