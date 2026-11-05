"""GSI 文档按团队生效关系只读接口（``/gsi/km/doc-publication``）。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_schema import PageResultSchema, PaginationQueryParam
from app.core.dependencies import db_getter
from app.core.router_class import ApiLogRoute

from .schema import (
    SbpKmDocPublicationOutSchema,
    SbpKmDocPublicationQueryParam,
    SbpKmDocPublicationWithTeamOutSchema,
    SbpKmDocPublicationWithTeamQueryParam,
)
from .service import SbpKmDocPublicationService

SbpKmDocPublicationRouter = APIRouter(
    route_class=ApiLogRoute, prefix="/km/doc-publication", tags=["207-GSI文档生效关系"]
)


@SbpKmDocPublicationRouter.get(
    "/list",
    summary="01-分页查询GSI文档生效关系",
    response_model=ResponseSchema[PageResultSchema[SbpKmDocPublicationOutSchema]],
)
async def get_sbp_km_doc_publication_list_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    page: Annotated[PaginationQueryParam, Depends()],
    search: Annotated[SbpKmDocPublicationQueryParam, Query()],
) -> JSONResponse:
    result_dict = await SbpKmDocPublicationService(db).page(
        page_no=page.page_no,
        page_size=page.page_size,
        search=search,
        order_by=page.order_by,
    )
    return SuccessResponse(data=result_dict, msg="查询GSI文档生效关系成功")


@SbpKmDocPublicationRouter.get(
    "/page-with-team",
    summary="02-分页查询生效关系（含团队编码与名称）",
    response_model=ResponseSchema[
        PageResultSchema[SbpKmDocPublicationWithTeamOutSchema]
    ],
)
async def get_sbp_km_doc_publication_page_with_team_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    page: Annotated[PaginationQueryParam, Depends()],
    search: Annotated[SbpKmDocPublicationWithTeamQueryParam, Query()],
) -> JSONResponse:
    result_dict = await SbpKmDocPublicationService(db).page_with_team(
        page_no=page.page_no,
        page_size=page.page_size,
        search=search,
    )
    return SuccessResponse(data=result_dict, msg="查询生效关系（含团队标识）成功")
