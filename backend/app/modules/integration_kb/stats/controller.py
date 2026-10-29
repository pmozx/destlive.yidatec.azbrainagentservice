"""知识库统计接口（/integration-kb/stats/*）。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import ResponseSchema, SuccessResponse
from app.core.dependencies import db_getter
from app.core.router_class import ApiLogRoute

from .schema import (
    DocumentStatusStatSchema,
    HotDocumentStatSchema,
    SpaceStatSchema,
    StatsOverviewOutSchema,
    TrendPointSchema,
)
from .service import IntegrationKbStatsService

IntegrationKbStatsRouter = APIRouter(route_class=ApiLogRoute, prefix="/stats", tags=["206-知识库-运营统计"])


@IntegrationKbStatsRouter.get("/overview", summary="01-知识库总览指标", response_model=ResponseSchema[StatsOverviewOutSchema])
async def get_stats_overview_controller(
        db: Annotated[AsyncSession, Depends(db_getter)],
) -> JSONResponse:
    result_dict = await IntegrationKbStatsService(db).overview()
    return SuccessResponse(data=result_dict, msg="获取知识库总览成功")


@IntegrationKbStatsRouter.get("/document-status", summary="02-文档解析状态分布", response_model=ResponseSchema[list[DocumentStatusStatSchema]])
async def get_stats_document_status_controller(
        db: Annotated[AsyncSession, Depends(db_getter)],
) -> JSONResponse:
    result_dict = await IntegrationKbStatsService(db).document_status()
    return SuccessResponse(data=result_dict, msg="获取文档状态分布成功")


@IntegrationKbStatsRouter.get("/hot-documents", summary="03-文档引用排行", response_model=ResponseSchema[list[HotDocumentStatSchema]])
async def get_stats_hot_documents_controller(
        db: Annotated[AsyncSession, Depends(db_getter)],
        limit: Annotated[int, Query(description="返回条数", ge=1, le=50)] = 10,
) -> JSONResponse:
    result_dict = await IntegrationKbStatsService(db).hot_documents(limit=limit)
    return SuccessResponse(data=result_dict, msg="获取文档引用排行成功")


@IntegrationKbStatsRouter.get("/space-ranking", summary="04-空间知识量排行", response_model=ResponseSchema[list[SpaceStatSchema]])
async def get_stats_space_ranking_controller(
        db: Annotated[AsyncSession, Depends(db_getter)],
        limit: Annotated[int, Query(description="返回条数", ge=1, le=50)] = 10,
) -> JSONResponse:
    result_dict = await IntegrationKbStatsService(db).space_ranking(limit=limit)
    return SuccessResponse(data=result_dict, msg="获取空间知识量排行成功")


@IntegrationKbStatsRouter.get("/upload-trend", summary="05-近N天上传趋势", response_model=ResponseSchema[list[TrendPointSchema]])
async def get_stats_upload_trend_controller(
        db: Annotated[AsyncSession, Depends(db_getter)],
        days: Annotated[int, Query(description="统计天数", ge=1, le=90)] = 7,
) -> JSONResponse:
    result_dict = await IntegrationKbStatsService(db).upload_trend(days=days)
    return SuccessResponse(data=result_dict, msg="获取上传趋势成功")
