"""知识任务接口（/integration-kb/task/*）。"""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_schema import PageResultSchema, PaginationQueryParam
from app.core.dependencies import db_getter
from app.core.router_class import ApiLogRoute

from .schema import TaskDetailOutSchema, TaskOutSchema, TaskQueryParam
from .service import IntegrationKbTaskService

IntegrationKbTaskRouter = APIRouter(route_class=ApiLogRoute, prefix="/task", tags=["204-知识库-解析任务"])


@IntegrationKbTaskRouter.get("/list", summary="01-分页查询解析任务", response_model=ResponseSchema[PageResultSchema[TaskOutSchema]])
async def get_task_list_controller(
        db: Annotated[AsyncSession, Depends(db_getter)],
        page: Annotated[PaginationQueryParam, Depends()],
        search: Annotated[TaskQueryParam, Query()],
) -> JSONResponse:
    result_dict = await IntegrationKbTaskService(db).page(
        page_no=page.page_no,
        page_size=page.page_size,
        search=search,
        order_by=page.order_by,
    )
    return SuccessResponse(data=result_dict, msg="查询解析任务成功")


@IntegrationKbTaskRouter.get("/detail/{id}", summary="02-查询解析任务详情", response_model=ResponseSchema[TaskDetailOutSchema])
async def get_task_detail_controller(
        db: Annotated[AsyncSession, Depends(db_getter)],
        id: Annotated[int, Path(description="任务ID", ge=1)],
) -> JSONResponse:
    result_dict = await IntegrationKbTaskService(db).detail(id=id)
    return SuccessResponse(data=result_dict, msg="获取解析任务详情成功")


@IntegrationKbTaskRouter.post("/retry/{id}", summary="03-重试失败节点", response_model=ResponseSchema[TaskOutSchema])
async def retry_task_controller(
        db: Annotated[AsyncSession, Depends(db_getter)],
        id: Annotated[int, Path(description="任务ID", ge=1)],
) -> JSONResponse:
    result_dict = await IntegrationKbTaskService(db).retry(id=id)
    return SuccessResponse(data=result_dict, msg="已提交失败节点重试，解析产物与分片将复用")


@IntegrationKbTaskRouter.delete("/delete", summary="04-删除解析任务记录", response_model=ResponseSchema[None])
async def delete_task_controller(
        db: Annotated[AsyncSession, Depends(db_getter)],
        ids: Annotated[list[int], Body(description="任务ID列表")],
) -> JSONResponse:
    await IntegrationKbTaskService(db).delete(ids=ids)
    return SuccessResponse(msg="删除解析任务记录成功")
