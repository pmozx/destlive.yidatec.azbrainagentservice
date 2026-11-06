"""解析任务管理 Controller：列表 + 详情 + 产物切片编辑 + 写入知识平台 + 上传提交。

上传端点与契约 ``#01`` 共用 ``parse_job.pipeline``：管理端自己发起一轮解析，
不必先把文件放到桶里再填 ``file_url``。

``POST /publish/{job_id}`` 是契约 03.1「新增已批准切片集合」的管理端等价入口：把产物
（含人工修订）的切片集合直接写成一版正式知识，不再重切。
"""

from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Body, Depends, File, Form, Path, Query, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_schema import PageResultSchema, PaginationQueryParam
from app.core.dependencies import db_getter
from app.core.router_class import ApiLogRoute

from .admin_service import KbParseJobAdminService, run_submitted_parse
from .schema import (
    KbParseChunkOutSchema,
    KbParseChunkUpdateSchema,
    KbParseJobDetailOutSchema,
    KbParseJobOutSchema,
    KbParseJobQueryParam,
    KbParsePublishOutSchema,
    KbParsePublishSchema,
    KbParseRetrySchema,
    KbParseStepOutSchema,
)

KbParseJobRouter = APIRouter(route_class=ApiLogRoute, prefix="/parse-jobs", tags=["202-解析任务"])


@KbParseJobRouter.get(
    "/list",
    summary="01-分页查询解析任务",
    response_model=ResponseSchema[PageResultSchema[KbParseJobOutSchema]],
)
async def get_parse_job_list_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    page: Annotated[PaginationQueryParam, Depends()],
    search: Annotated[KbParseJobQueryParam, Query()],
) -> JSONResponse:
    result = await KbParseJobAdminService(db).page(
        page_no=page.page_no,
        page_size=page.page_size,
        search=search,
        order_by=page.order_by,
    )
    return SuccessResponse(data=result, msg="查询解析任务成功")


@KbParseJobRouter.get(
    "/detail/{job_id}",
    summary="02-查询解析任务详情",
    response_model=ResponseSchema[KbParseJobDetailOutSchema],
)
async def get_parse_job_detail_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    job_id: Annotated[str, Path(description="契约 job_id", min_length=1, max_length=64)],
) -> JSONResponse:
    result = await KbParseJobAdminService(db).detail(job_id)
    return SuccessResponse(data=result, msg="获取解析任务详情成功")


@KbParseJobRouter.get(
    "/steps/{job_id}",
    summary="02-1 查询流水线步骤台账",
    response_model=ResponseSchema[list[KbParseStepOutSchema]],
)
async def get_parse_job_steps_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    job_id: Annotated[str, Path(description="契约 job_id", min_length=1, max_length=64)],
) -> JSONResponse:
    """单条任务的每步执行记录（含历史轮次）：耗时、失败原因、降级/跳过说明。"""
    result = await KbParseJobAdminService(db).list_steps(job_id)
    return SuccessResponse(data=result, msg="获取解析步骤成功")


@KbParseJobRouter.post(
    "/retry/{job_id}",
    summary="02-2 从失败步骤重跑解析任务",
    response_model=ResponseSchema[KbParseJobOutSchema],
)
async def retry_parse_job_controller(
    background: BackgroundTasks,
    db: Annotated[AsyncSession, Depends(db_getter)],
    job_id: Annotated[str, Path(description="契约 job_id", min_length=1, max_length=64)],
    data: Annotated[KbParseRetrySchema | None, Body(description="重跑起点（缺省=上一轮失败步骤）")] = None,
) -> JSONResponse:
    """重跑失败/卡住的任务：从失败那一步继续，之前的步骤复用台账里的产物快照。"""
    out, task = await KbParseJobAdminService(db).retry(job_id, from_step=data.from_step if data else None)
    background.add_task(run_submitted_parse, task)
    return SuccessResponse(data=out, msg="重跑已受理")


@KbParseJobRouter.post(
    "/upload",
    summary="03-上传文件并提交解析",
    response_model=ResponseSchema[KbParseJobOutSchema],
)
async def upload_parse_job_controller(
    background: BackgroundTasks,
    db: Annotated[AsyncSession, Depends(db_getter)],
    file: Annotated[UploadFile, File(description="待解析文件（docx/pdf/txt/md/xlsx 等白名单后缀）")],
    resource_type: Annotated[Literal["document", "faq"], Form(description="document/faq")] = "document",
    chunk: Annotated[bool, Form(description="是否生成切片（FAQ 按问答提取，忽略此项）")] = True,
    language: Annotated[str, Form(max_length=16, description="FAQ 提取语言")] = "zh-CN",
    prompt_text: Annotated[str | None, Form(max_length=4000, description="FAQ 提取提示词")] = None,
    model_key: Annotated[str | None, Form(max_length=64, description="模型键")] = None,
) -> JSONResponse:
    """同步校验与落原件、建任务，解析在响应后执行；失败属于任务结果（列表看 ``error_code``）。"""
    out, task = await KbParseJobAdminService(db).submit_upload(
        file,
        resource_type=resource_type,
        chunk_enabled=chunk,
        language=language,
        prompt_text=prompt_text,
        model_key=model_key,
    )
    if task is not None:
        background.add_task(run_submitted_parse, task)
    return SuccessResponse(data=out, msg="解析任务已受理")


@KbParseJobRouter.put(
    "/update-chunk/{job_id}",
    summary="04-编辑解析产物切片",
    response_model=ResponseSchema[KbParseChunkOutSchema],
)
async def update_parse_chunk_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    job_id: Annotated[str, Path(description="契约 job_id", min_length=1, max_length=64)],
    data: Annotated[KbParseChunkUpdateSchema, Body(description="待编辑的切片")],
) -> JSONResponse:
    """按 ``seq`` 覆盖单条切片的正文（可选标题），其余切片与任务状态不动。"""
    result = await KbParseJobAdminService(db).update_chunk(job_id, data)
    return SuccessResponse(data=result, msg="更新解析产物切片成功")


@KbParseJobRouter.post(
    "/publish/{job_id}",
    summary="05-写入知识平台（03.1 等价）",
    response_model=ResponseSchema[KbParsePublishOutSchema],
)
async def publish_parse_chunks_controller(
    db: Annotated[AsyncSession, Depends(db_getter)],
    job_id: Annotated[str, Path(description="契约 job_id", min_length=1, max_length=64)],
    data: Annotated[KbParsePublishSchema, Body(description="发布单元展示字段（缺省值由服务端推导）")],
) -> JSONResponse:
    """把解析产物里已批准的切片集合写成一版正式知识；重复提交返回 409。"""
    result = await KbParseJobAdminService(db).publish_chunks(job_id, data)
    return SuccessResponse(data=result, msg="解析产物已写入知识平台")
