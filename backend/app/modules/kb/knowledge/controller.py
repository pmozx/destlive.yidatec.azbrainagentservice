"""知识维护 Controller：RESTful 路由 + 统一响应封装 + 标准接口文档注解。

路由前缀 ``/kb/knowledge``（域前缀由 ``app/api/v1/routers.py`` 挂载），整组接口挂
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
    KbKnowledgeChunkDetailOutSchema,
    KbKnowledgeChunkOutSchema,
    KbKnowledgeCreateSchema,
    KbKnowledgeDetailOutSchema,
    KbKnowledgeOutSchema,
    KbKnowledgeQueryParam,
    KbKnowledgeRevisionDetailOutSchema,
    KbKnowledgeRevisionOutSchema,
    KbKnowledgeStatusSchema,
    KbKnowledgeUpdateSchema,
)
from .service import KbKnowledgeService

KbKnowledgeRouter = APIRouter(route_class=ApiLogRoute, prefix="/knowledge", tags=["201-知识维护"])


@KbKnowledgeRouter.get(
    "/list",
    summary="01-分页查询知识条目",
    response_model=ResponseSchema[PageResultSchema[KbKnowledgeOutSchema]],
)
async def get_knowledge_list_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    page: Annotated[PaginationQueryParam, Depends()],
    search: Annotated[KbKnowledgeQueryParam, Query()],
) -> JSONResponse:
    result = await KbKnowledgeService(db).page(
        page_no=page.page_no,
        page_size=page.page_size,
        search=search,
        order_by=page.order_by,
    )
    return SuccessResponse(data=result, msg="查询知识条目成功")


@KbKnowledgeRouter.get(
    "/detail/{publication_id}",
    summary="02-查询知识条目详情",
    response_model=ResponseSchema[KbKnowledgeDetailOutSchema],
)
async def get_knowledge_detail_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    publication_id: Annotated[str, Path(description="发布单元 ID", min_length=1, max_length=64)],
) -> JSONResponse:
    result = await KbKnowledgeService(db).detail(publication_id)
    return SuccessResponse(data=result, msg="获取知识条目详情成功")


@KbKnowledgeRouter.post(
    "/create",
    summary="03-新增知识条目",
    response_model=ResponseSchema[KbKnowledgeDetailOutSchema],
)
async def create_knowledge_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    data: Annotated[KbKnowledgeCreateSchema, Body(description="知识条目信息")],
) -> JSONResponse:
    result = await KbKnowledgeService(db).create(data)
    return SuccessResponse(data=result, msg="新增知识条目成功")


@KbKnowledgeRouter.put(
    "/update/{publication_id}",
    summary="04-编辑知识条目",
    response_model=ResponseSchema[KbKnowledgeDetailOutSchema],
)
async def update_knowledge_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    publication_id: Annotated[str, Path(description="发布单元 ID", min_length=1, max_length=64)],
    data: Annotated[KbKnowledgeUpdateSchema, Body(description="待更新字段")],
) -> JSONResponse:
    result = await KbKnowledgeService(db).update(publication_id, data)
    return SuccessResponse(data=result, msg="编辑知识条目成功")


@KbKnowledgeRouter.patch(
    "/status/{publication_id}",
    summary="05-变更知识条目状态",
    response_model=ResponseSchema[KbKnowledgeDetailOutSchema],
)
async def update_knowledge_status_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    publication_id: Annotated[str, Path(description="发布单元 ID", min_length=1, max_length=64)],
    data: Annotated[KbKnowledgeStatusSchema, Body(description="目标状态")],
) -> JSONResponse:
    result = await KbKnowledgeService(db).update_status(publication_id, data)
    return SuccessResponse(data=result, msg="变更知识条目状态成功")


@KbKnowledgeRouter.delete(
    "/delete/{publication_id}",
    summary="06-删除知识条目",
    response_model=ResponseSchema[None],
)
async def delete_knowledge_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    publication_id: Annotated[str, Path(description="发布单元 ID", min_length=1, max_length=64)],
) -> JSONResponse:
    await KbKnowledgeService(db).delete(publication_id)
    return SuccessResponse(msg="删除知识条目成功")


@KbKnowledgeRouter.get(
    "/versions/{publication_id}",
    summary="07-分页查询知识版本快照",
    response_model=ResponseSchema[PageResultSchema[KbKnowledgeRevisionOutSchema]],
)
async def get_knowledge_versions_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    publication_id: Annotated[str, Path(description="发布单元 ID", min_length=1, max_length=64)],
    page: Annotated[PaginationQueryParam, Depends()],
) -> JSONResponse:
    result = await KbKnowledgeService(db).list_revisions(
        publication_id=publication_id,
        page_no=page.page_no,
        page_size=page.page_size,
        order_by=page.order_by,
    )
    return SuccessResponse(data=result, msg="查询知识版本快照成功")


@KbKnowledgeRouter.get(
    "/versions/detail/{revision_id}",
    summary="08-查询知识版本快照详情",
    response_model=ResponseSchema[KbKnowledgeRevisionDetailOutSchema],
)
async def get_knowledge_version_detail_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    revision_id: Annotated[int, Path(description="版本快照主键", ge=1)],
) -> JSONResponse:
    result = await KbKnowledgeService(db).detail_revision(revision_id)
    return SuccessResponse(data=result, msg="获取知识版本快照详情成功")


@KbKnowledgeRouter.get(
    "/chunks/{publication_id}",
    summary="09-分页查询知识切片",
    response_model=ResponseSchema[PageResultSchema[KbKnowledgeChunkOutSchema]],
)
async def get_knowledge_chunks_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    publication_id: Annotated[str, Path(description="发布单元 ID", min_length=1, max_length=64)],
    page: Annotated[PaginationQueryParam, Depends()],
    record_id: Annotated[str | None, Query(description="只看某个版本快照")] = None,
) -> JSONResponse:
    result = await KbKnowledgeService(db).list_chunks(
        publication_id=publication_id,
        record_id=record_id,
        page_no=page.page_no,
        page_size=page.page_size,
        order_by=page.order_by,
    )
    return SuccessResponse(data=result, msg="查询知识切片成功")


@KbKnowledgeRouter.get(
    "/chunks/detail/{chunk_id}",
    summary="10-查询知识切片详情",
    response_model=ResponseSchema[KbKnowledgeChunkDetailOutSchema],
)
async def get_knowledge_chunk_detail_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    chunk_id: Annotated[int, Path(description="切片主键", ge=1)],
) -> JSONResponse:
    result = await KbKnowledgeService(db).detail_chunk(chunk_id)
    return SuccessResponse(data=result, msg="获取知识切片详情成功")
