"""GSI FAQ 按团队生效关系只读接口（``/gsi/faq/publication``）。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_schema import PageResultSchema, PaginationQueryParam
from app.core.dependencies import db_getter
from app.core.router_class import ApiLogRoute

from .schema import SbpFaqPublicationOutSchema, SbpFaqPublicationQueryParam
from .service import SbpFaqPublicationService

SbpFaqPublicationRouter = APIRouter(
    route_class=ApiLogRoute,
    prefix="/faq/publication",
    tags=["210-FAQ发布生效关系"],
)


@SbpFaqPublicationRouter.get(
    "/list",
    summary="01-分页查询FAQ发布生效关系（含生效规则解析）",
    response_model=ResponseSchema[PageResultSchema[SbpFaqPublicationOutSchema]],
)
async def get_sbp_faq_publication_list_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    page: Annotated[PaginationQueryParam, Depends()],
    search: Annotated[SbpFaqPublicationQueryParam, Query()],
) -> JSONResponse:
    result_dict = await SbpFaqPublicationService(db).page(
        page_no=page.page_no,
        page_size=page.page_size,
        search=search,
    )
    return SuccessResponse(data=result_dict, msg="查询FAQ发布生效关系成功")
