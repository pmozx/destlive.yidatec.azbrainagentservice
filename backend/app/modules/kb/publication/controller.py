"""发布单元 Controller：RESTful 路由 + 统一响应封装 + 标准接口文档注解。

路由前缀 ``/kb/publication``（域前缀由 ``app/api/v1/routers.py`` 挂载），整组接口挂
``ApiLogRoute`` 自动留痕；业务异常由全局 handler 统一转统一响应结构。
"""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_schema import PageResultSchema, PaginationQueryParam
from app.core.dependencies import db_getter
from app.core.router_class import ApiLogRoute

from .schema import (
    KbPublicationBaselineOutSchema,
    KbPublicationChunkDetailOutSchema,
    KbPublicationChunkOutSchema,
    KbPublicationCreateSchema,
    KbPublicationDetailOutSchema,
    KbPublicationOutSchema,
    KbPublicationQueryParam,
    KbPublicationRevisionDetailOutSchema,
    KbPublicationRevisionOutSchema,
    KbPublicationSnapshotDeleteOutSchema,
    KbPublicationSnapshotDeleteSchema,
    KbPublicationStatusSchema,
    KbPublicationUpdateSchema,
)
from .service import KbPublicationService

KbPublicationRouter = APIRouter(route_class=ApiLogRoute, prefix="/publication", tags=["201-发布单元"])


@KbPublicationRouter.get(
    "/list",
    summary="01-分页查询发布单元",
    response_model=ResponseSchema[PageResultSchema[KbPublicationOutSchema]],
)
async def get_publication_list_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    page: Annotated[PaginationQueryParam, Depends()],
    search: Annotated[KbPublicationQueryParam, Query()],
) -> JSONResponse:
    result = await KbPublicationService(db).page(
        page_no=page.page_no,
        page_size=page.page_size,
        search=search,
        order_by=page.order_by,
    )
    return SuccessResponse(data=result, msg="查询发布单元成功")


@KbPublicationRouter.get(
    "/detail/{publication_id}",
    summary="02-查询发布单元详情",
    response_model=ResponseSchema[KbPublicationDetailOutSchema],
)
async def get_publication_detail_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    publication_id: Annotated[str, Path(description="发布单元 ID", min_length=1, max_length=64)],
) -> JSONResponse:
    result = await KbPublicationService(db).detail(publication_id)
    return SuccessResponse(data=result, msg="获取发布单元详情成功")


@KbPublicationRouter.post(
    "/create",
    summary="03-新增发布单元",
    response_model=ResponseSchema[KbPublicationDetailOutSchema],
)
async def create_publication_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    data: Annotated[KbPublicationCreateSchema, Body(description="发布单元信息")],
) -> JSONResponse:
    result = await KbPublicationService(db).create(data)
    return SuccessResponse(data=result, msg="新增发布单元成功")


@KbPublicationRouter.put(
    "/update/{publication_id}",
    summary="04-编辑发布单元",
    response_model=ResponseSchema[KbPublicationDetailOutSchema],
)
async def update_publication_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    publication_id: Annotated[str, Path(description="发布单元 ID", min_length=1, max_length=64)],
    data: Annotated[KbPublicationUpdateSchema, Body(description="待更新字段")],
) -> JSONResponse:
    result = await KbPublicationService(db).update(publication_id, data)
    return SuccessResponse(data=result, msg="编辑发布单元成功")


@KbPublicationRouter.patch(
    "/status/{publication_id}",
    summary="05-变更发布单元状态",
    response_model=ResponseSchema[KbPublicationDetailOutSchema],
)
async def update_publication_status_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    publication_id: Annotated[str, Path(description="发布单元 ID", min_length=1, max_length=64)],
    data: Annotated[KbPublicationStatusSchema, Body(description="目标状态")],
) -> JSONResponse:
    result = await KbPublicationService(db).update_status(publication_id, data)
    return SuccessResponse(data=result, msg="变更发布单元状态成功")


@KbPublicationRouter.delete(
    "/delete/{publication_id}",
    summary="06-删除发布单元",
    response_model=ResponseSchema[None],
)
async def delete_publication_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    publication_id: Annotated[str, Path(description="发布单元 ID", min_length=1, max_length=64)],
) -> JSONResponse:
    await KbPublicationService(db).delete(publication_id)
    return SuccessResponse(msg="删除发布单元成功")


@KbPublicationRouter.get(
    "/versions/{publication_id}",
    summary="07-分页查询版本快照",
    response_model=ResponseSchema[PageResultSchema[KbPublicationRevisionOutSchema]],
)
async def get_publication_versions_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    publication_id: Annotated[str, Path(description="发布单元 ID", min_length=1, max_length=64)],
    page: Annotated[PaginationQueryParam, Depends()],
) -> JSONResponse:
    result = await KbPublicationService(db).list_revisions(
        publication_id=publication_id,
        page_no=page.page_no,
        page_size=page.page_size,
        order_by=page.order_by,
    )
    return SuccessResponse(data=result, msg="查询版本快照成功")


@KbPublicationRouter.get(
    "/versions/detail/{revision_id}",
    summary="08-查询版本快照详情",
    response_model=ResponseSchema[KbPublicationRevisionDetailOutSchema],
)
async def get_publication_version_detail_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    revision_id: Annotated[int, Path(description="版本快照主键", ge=1)],
) -> JSONResponse:
    result = await KbPublicationService(db).detail_revision(revision_id)
    return SuccessResponse(data=result, msg="获取版本快照详情成功")


@KbPublicationRouter.get(
    "/chunks/{publication_id}",
    summary="09-分页查询知识切片",
    response_model=ResponseSchema[PageResultSchema[KbPublicationChunkOutSchema]],
)
async def get_publication_chunks_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    publication_id: Annotated[str, Path(description="发布单元 ID", min_length=1, max_length=64)],
    page: Annotated[PaginationQueryParam, Depends()],
    record_id: Annotated[str | None, Query(description="只看某个版本快照")] = None,
) -> JSONResponse:
    result = await KbPublicationService(db).list_chunks(
        publication_id=publication_id,
        record_id=record_id,
        page_no=page.page_no,
        page_size=page.page_size,
        order_by=page.order_by,
    )
    return SuccessResponse(data=result, msg="查询知识切片成功")


@KbPublicationRouter.get(
    "/chunks/detail/{chunk_id}",
    summary="10-查询知识切片详情",
    response_model=ResponseSchema[KbPublicationChunkDetailOutSchema],
)
async def get_publication_chunk_detail_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    chunk_id: Annotated[int, Path(description="切片主键", ge=1)],
) -> JSONResponse:
    result = await KbPublicationService(db).detail_chunk(chunk_id)
    return SuccessResponse(data=result, msg="获取知识切片详情成功")


@KbPublicationRouter.get(
    "/baseline/{publication_id}",
    summary="11-读取已批准基线（03.2）",
    response_model=ResponseSchema[KbPublicationBaselineOutSchema],
)
async def get_publication_baseline_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    publication_id: Annotated[str, Path(description="发布单元 ID", min_length=1, max_length=64)],
    record_id: Annotated[str | None, Query(description="快照 ID，省略读当前生效快照")] = None,
    cursor: Annotated[str | None, Query(description="上一页返回的 next_cursor")] = None,
    limit: Annotated[int, Query(description="每页切片数", ge=1, le=500)] = 100,
) -> JSONResponse:
    result = await KbPublicationService(db).read_baseline(
        publication_id=publication_id,
        record_id=record_id,
        cursor=cursor,
        limit=limit,
    )
    return SuccessResponse(data=result, msg="读取已批准基线成功")


@KbPublicationRouter.delete(
    "/snapshot/{publication_id}",
    summary="12-删除已批准切片集合（03.4）",
    response_model=ResponseSchema[KbPublicationSnapshotDeleteOutSchema],
)
async def delete_publication_snapshot_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    publication_id: Annotated[str, Path(description="发布单元 ID", min_length=1, max_length=64)],
    data: Annotated[KbPublicationSnapshotDeleteSchema, Body(description="待删除快照与原因")],
) -> JSONResponse:
    result = await KbPublicationService(db).delete_snapshot(publication_id, data)
    return SuccessResponse(data=result, msg="删除已批准切片集合成功")
