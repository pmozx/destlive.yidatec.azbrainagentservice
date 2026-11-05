"""解析任务管理 Controller：列表 + 详情 + 上传提交（后台解析由流水线驱动）。

上传端点与契约 ``#01`` 共用 ``parse_job.pipeline``：管理端自己发起一轮解析，
不必先把文件放到桶里再填 ``file_url``。
"""

from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Path, Query, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_schema import PageResultSchema, PaginationQueryParam
from app.core.dependencies import db_getter
from app.core.router_class import ApiLogRoute

from .admin_service import KbParseJobAdminService, run_submitted_parse
from .schema import KbParseJobDetailOutSchema, KbParseJobOutSchema, KbParseJobQueryParam

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
