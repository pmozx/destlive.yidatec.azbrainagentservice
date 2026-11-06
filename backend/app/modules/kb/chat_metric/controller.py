"""问答指标 Controller：管理端只读（按会话看单轮指标 / 看会话维度汇总）。

路由前缀 ``/kb/chat-metric``，整组接口挂 ``ApiLogRoute`` 自动留痕。指标只由问答链路写入
（``chat_metric/meter.py``，写失败只告警），管理端不提供任何写入口——指标是事实快照，
「补一条」或「改一条」都会让它失去作为事实的资格。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_schema import PageResultSchema, PaginationQueryParam
from app.core.dependencies import db_getter
from app.core.router_class import ApiLogRoute

from .admin_service import KbChatTurnMetricAdminService
from .schema import (
    KbChatTurnMetricOutSchema,
    KbChatTurnMetricQueryParam,
    KbChatTurnMetricSummaryOutSchema,
    KbChatTurnMetricSummaryQueryParam,
)

KbChatTurnMetricRouter = APIRouter(route_class=ApiLogRoute, prefix="/chat-metric", tags=["207-问答指标"])


@KbChatTurnMetricRouter.get(
    "/list",
    summary="01-分页查询单轮指标",
    response_model=ResponseSchema[PageResultSchema[KbChatTurnMetricOutSchema]],
)
async def get_turn_metric_list_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    page: Annotated[PaginationQueryParam, Depends()],
    search: Annotated[KbChatTurnMetricQueryParam, Query()],
) -> JSONResponse:
    result = await KbChatTurnMetricAdminService(db).page(
        page_no=page.page_no,
        page_size=page.page_size,
        session_id=search.session_id,
    )
    return SuccessResponse(data=result, msg="查询单轮指标成功")


@KbChatTurnMetricRouter.get(
    "/summary",
    summary="02-查询会话维度指标汇总",
    response_model=ResponseSchema[KbChatTurnMetricSummaryOutSchema],
)
async def get_turn_metric_summary_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    search: Annotated[KbChatTurnMetricSummaryQueryParam, Query()],
) -> JSONResponse:
    result = await KbChatTurnMetricAdminService(db).summary(search)
    return SuccessResponse(data=result, msg="查询指标汇总成功")
