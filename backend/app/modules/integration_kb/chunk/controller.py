"""知识分片接口（/integration-kb/chunk/*）。"""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_schema import PageResultSchema, PaginationQueryParam
from app.core.dependencies import db_getter
from app.core.router_class import ApiLogRoute

from .schema import ChunkOutSchema, ChunkQueryParam
from .service import IntegrationKbChunkService

IntegrationKbChunkRouter = APIRouter(route_class=ApiLogRoute, prefix="/chunk", tags=["205-知识库-知识分片"])


@IntegrationKbChunkRouter.get("/list", summary="01-分页查询知识分片", response_model=ResponseSchema[PageResultSchema[ChunkOutSchema]])
async def get_chunk_list_controller(
        db: Annotated[AsyncSession, Depends(db_getter)],
        page: Annotated[PaginationQueryParam, Depends()],
        search: Annotated[ChunkQueryParam, Query()],
) -> JSONResponse:
    result_dict = await IntegrationKbChunkService(db).page(
        page_no=page.page_no,
        page_size=page.page_size,
        search=search,
        order_by=page.order_by,
    )
    return SuccessResponse(data=result_dict, msg="查询知识分片成功")


@IntegrationKbChunkRouter.delete("/delete", summary="02-删除知识分片", response_model=ResponseSchema[None])
async def delete_chunk_controller(
        db: Annotated[AsyncSession, Depends(db_getter)],
        ids: Annotated[list[str], Body(description="分片ID列表")],
) -> JSONResponse:
    await IntegrationKbChunkService(db).delete(ids=ids)
    return SuccessResponse(msg="删除知识分片成功")
