"""重复分组治理 Controller：列表 + 详情 + 标记处理 + 主动治理（检测/查重/合并预览）。

主动治理三个端点对应契约 #16（保存前查重）、#17（触发检测）、#19（合并预览）：
契约侧由 GSI 随请求签发 ``scope``，管理端改由 ``publication_ids`` 表达范围，后端反查生成。
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
    KbDupCheckOutSchema,
    KbDupCheckSchema,
    KbDupDetectOutSchema,
    KbDupDetectSchema,
    KbDupGroupDetailOutSchema,
    KbDupGroupOutSchema,
    KbDupGroupQueryParam,
    KbDupGroupStatusSchema,
    KbDupPreviewOutSchema,
    KbDupPreviewSchema,
)
from .service import KbDupGroupService

KbDupGroupRouter = APIRouter(route_class=ApiLogRoute, prefix="/dup-groups", tags=["203-重复治理"])


@KbDupGroupRouter.get(
    "/list",
    summary="01-分页查询重复分组",
    response_model=ResponseSchema[PageResultSchema[KbDupGroupOutSchema]],
)
async def get_dup_group_list_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    page: Annotated[PaginationQueryParam, Depends()],
    search: Annotated[KbDupGroupQueryParam, Query()],
) -> JSONResponse:
    result = await KbDupGroupService(db).page(
        page_no=page.page_no,
        page_size=page.page_size,
        search=search,
        order_by=page.order_by,
    )
    return SuccessResponse(data=result, msg="查询重复分组成功")


@KbDupGroupRouter.get(
    "/detail/{group_id}",
    summary="02-查询重复分组详情",
    response_model=ResponseSchema[KbDupGroupDetailOutSchema],
)
async def get_dup_group_detail_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    group_id: Annotated[int, Path(description="分组主键", ge=1)],
) -> JSONResponse:
    result = await KbDupGroupService(db).detail(group_id)
    return SuccessResponse(data=result, msg="获取重复分组详情成功")


@KbDupGroupRouter.patch(
    "/status/{group_id}",
    summary="03-标记重复分组处理结果",
    response_model=ResponseSchema[KbDupGroupDetailOutSchema],
)
async def update_dup_group_status_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    group_id: Annotated[int, Path(description="分组主键", ge=1)],
    data: Annotated[KbDupGroupStatusSchema, Body(description="merged/dismissed")],
) -> JSONResponse:
    result = await KbDupGroupService(db).update_status(group_id, data)
    return SuccessResponse(data=result, msg="标记重复分组处理结果成功")


@KbDupGroupRouter.post(
    "/detect",
    summary="04-发起重复检测（落快照并物化分组）",
    response_model=ResponseSchema[KbDupDetectOutSchema],
)
async def detect_dup_groups_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    data: Annotated[KbDupDetectSchema, Body(description="治理范围与策略")],
) -> JSONResponse:
    """按发布单元反查范围后全量比对：产出的分组写入治理表，页面刷新即可看到。"""
    result = await KbDupGroupService(db).detect(data)
    return SuccessResponse(data=result, msg="重复检测完成")


@KbDupGroupRouter.post(
    "/check",
    summary="05-保存前查重（候选内容 vs 治理范围）",
    response_model=ResponseSchema[KbDupCheckOutSchema],
)
async def check_duplicate_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    data: Annotated[KbDupCheckSchema, Body(description="候选内容与治理范围")],
) -> JSONResponse:
    """只报命中、不阻止保存；候选内容不落任何正式表。"""
    result = await KbDupGroupService(db).check(data)
    return SuccessResponse(data=result, msg="保存前查重完成")


@KbDupGroupRouter.post(
    "/preview/{group_id}",
    summary="06-生成合并预览（草稿态，不落正式库）",
    response_model=ResponseSchema[KbDupPreviewOutSchema],
)
async def preview_merge_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    group_id: Annotated[int, Path(description="分组主键", ge=1)],
    data: Annotated[KbDupPreviewSchema, Body(description="保留项与处置动作")],
) -> JSONResponse:
    """范围与 result_id 取自分组行；结果写回 ``merged_preview``，不进入正式知识库。"""
    result = await KbDupGroupService(db).preview(group_id, data)
    return SuccessResponse(data=result, msg="合并预览生成成功")
