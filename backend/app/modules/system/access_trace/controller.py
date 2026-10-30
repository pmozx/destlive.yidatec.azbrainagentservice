"""接入调用追踪接口（/system/access-trace/*）：只读，供系统管理页排查 GSI 契约调用。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_schema import PageResultSchema, PaginationQueryParam
from app.core.dependencies import db_getter
from app.core.router_class import ApiLogRoute

from .schema import (
    AccessTraceDetailOutSchema,
    AccessTraceOutSchema,
    AccessTraceQueryParam,
    IdempotencyOutSchema,
    IdempotencyQueryParam,
    NonceOutSchema,
    NonceQueryParam,
    RequestLogOutSchema,
    RequestLogQueryParam,
    TraceSummarySchema,
    TraceTimelineSchema,
)
from .service import AccessTraceService

AccessTraceRouter = APIRouter(route_class=ApiLogRoute, prefix="/access-trace", tags=["102-接入调用追踪"])


@AccessTraceRouter.get("/summary", summary="01-近N小时接入概览", response_model=ResponseSchema[TraceSummarySchema])
async def get_access_trace_summary_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    hours: Annotated[int, Query(description="统计窗口（小时）", ge=1, le=168)] = 24,
) -> JSONResponse:
    result_dict = await AccessTraceService(db).summary(hours=hours)
    return SuccessResponse(data=result_dict, msg="获取接入概览成功")


@AccessTraceRouter.get("/calls", summary="02-分页查询契约调用明细", response_model=ResponseSchema[PageResultSchema[AccessTraceOutSchema]])
async def get_access_trace_calls_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    page: Annotated[PaginationQueryParam, Depends()],
    search: Annotated[AccessTraceQueryParam, Query()],
) -> JSONResponse:
    result_dict = await AccessTraceService(db).page_calls(
        page_no=page.page_no,
        page_size=page.page_size,
        search=search,
        order_by=page.order_by,
    )
    return SuccessResponse(data=result_dict, msg="查询契约调用明细成功")


@AccessTraceRouter.get("/calls/detail/{id}", summary="03-查询调用详情", response_model=ResponseSchema[AccessTraceDetailOutSchema])
async def get_access_trace_call_detail_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    id: Annotated[int, Path(description="日志ID", ge=1)],
) -> JSONResponse:
    result_dict = await AccessTraceService(db).call_detail(id=id)
    return SuccessResponse(data=result_dict, msg="获取调用详情成功")


@AccessTraceRouter.get("/nonces", summary="04-分页查询防重放台账", response_model=ResponseSchema[PageResultSchema[NonceOutSchema]])
async def get_access_trace_nonces_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    page: Annotated[PaginationQueryParam, Depends()],
    search: Annotated[NonceQueryParam, Query()],
) -> JSONResponse:
    result_dict = await AccessTraceService(db).page_nonces(
        page_no=page.page_no,
        page_size=page.page_size,
        search=search,
        order_by=page.order_by,
    )
    return SuccessResponse(data=result_dict, msg="查询防重放台账成功")


@AccessTraceRouter.get(
    "/idempotency", summary="05-分页查询幂等台账", response_model=ResponseSchema[PageResultSchema[IdempotencyOutSchema]]
)
async def get_access_trace_idempotency_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    page: Annotated[PaginationQueryParam, Depends()],
    search: Annotated[IdempotencyQueryParam, Query()],
) -> JSONResponse:
    result_dict = await AccessTraceService(db).page_idempotency(
        page_no=page.page_no,
        page_size=page.page_size,
        search=search,
        order_by=page.order_by,
    )
    return SuccessResponse(data=result_dict, msg="查询幂等台账成功")


@AccessTraceRouter.get(
    "/request-logs", summary="06-分页查询结果台账", response_model=ResponseSchema[PageResultSchema[RequestLogOutSchema]]
)
async def get_access_trace_request_logs_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    page: Annotated[PaginationQueryParam, Depends()],
    search: Annotated[RequestLogQueryParam, Query()],
) -> JSONResponse:
    result_dict = await AccessTraceService(db).page_request_logs(
        page_no=page.page_no,
        page_size=page.page_size,
        search=search,
        order_by=page.order_by,
    )
    return SuccessResponse(data=result_dict, msg="查询结果台账成功")


@AccessTraceRouter.get("/timeline", summary="07-按request_id串联四张表", response_model=ResponseSchema[TraceTimelineSchema])
async def get_access_trace_timeline_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    request_id: Annotated[str, Query(description="契约追踪 ID", min_length=1, max_length=64)],
) -> JSONResponse:
    result_dict = await AccessTraceService(db).timeline(request_id=request_id)
    return SuccessResponse(data=result_dict, msg="追踪成功")
