"""知识文档接口（/integration-kb/document/*）。"""

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Body, Depends, File, Form, Path, Query, UploadFile, status
from fastapi.responses import JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_schema import PageResultSchema, PaginationQueryParam
from app.core.dependencies import db_getter
from app.core.router_class import ApiLogRoute

from ..task.schema import TaskOutSchema
from .schema import (
    DocumentBatchAvailableSchema,
    DocumentDownloadSchema,
    DocumentOutSchema,
    DocumentPreviewOutSchema,
    DocumentQueryParam,
    DocumentUpdateSchema,
    DocumentUploadOutSchema,
)
from .service import IntegrationKbDocumentService

IntegrationKbDocumentRouter = APIRouter(route_class=ApiLogRoute, prefix="/document", tags=["203-知识库-知识文档"])


def _attachment_header(file_name: str) -> dict[str, str]:
    """中文文件名按 RFC 5987 编码，避免响应头出现非 ASCII 字符。"""
    return {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(file_name)}"}


@IntegrationKbDocumentRouter.get("/list", summary="01-分页查询知识文档", response_model=ResponseSchema[PageResultSchema[DocumentOutSchema]])
async def get_document_list_controller(
        db: Annotated[AsyncSession, Depends(db_getter)],
        page: Annotated[PaginationQueryParam, Depends()],
        search: Annotated[DocumentQueryParam, Query()],
) -> JSONResponse:
    result_dict = await IntegrationKbDocumentService(db).page(
        page_no=page.page_no,
        page_size=page.page_size,
        search=search,
        order_by=page.order_by,
    )
    return SuccessResponse(data=result_dict, msg="查询知识文档成功")


@IntegrationKbDocumentRouter.get("/detail/{id}", summary="02-查询知识文档详情", response_model=ResponseSchema[DocumentOutSchema])
async def get_document_detail_controller(
        db: Annotated[AsyncSession, Depends(db_getter)],
        id: Annotated[int, Path(description="文档ID", ge=1)],
) -> JSONResponse:
    result_dict = await IntegrationKbDocumentService(db).detail(id=id)
    return SuccessResponse(data=result_dict, msg="获取知识文档详情成功")


@IntegrationKbDocumentRouter.post(
    "/upload",
    status_code=status.HTTP_201_CREATED,
    summary="03-批量上传知识文档",
    response_model=ResponseSchema[DocumentUploadOutSchema],
)
async def upload_document_controller(
        db: Annotated[AsyncSession, Depends(db_getter)],
        files: Annotated[list[UploadFile], File(description="待上传的文档（可多选）")],
        space_id: Annotated[int, Form(description="所属空间ID", ge=1)],
        category_id: Annotated[int | None, Form(description="所属分类ID（不传=未分类）")] = None,
) -> JSONResponse:
    result_dict = await IntegrationKbDocumentService(db).upload(files=files, space_id=space_id, category_id=category_id)
    return SuccessResponse(data=result_dict, msg="上传完成（失败明细见返回结果）")


@IntegrationKbDocumentRouter.put("/update/{id}", summary="04-修改知识文档", response_model=ResponseSchema[DocumentOutSchema])
async def update_document_controller(
        db: Annotated[AsyncSession, Depends(db_getter)],
        id: Annotated[int, Path(description="文档ID", ge=1)],
        data: Annotated[DocumentUpdateSchema, Body(description="知识文档修改参数")],
) -> JSONResponse:
    result_dict = await IntegrationKbDocumentService(db).update(id=id, data=data)
    return SuccessResponse(data=result_dict, msg="修改知识文档成功")


@IntegrationKbDocumentRouter.put("/batch-available", summary="05-批量上下架知识文档", response_model=ResponseSchema[None])
async def batch_available_document_controller(
        db: Annotated[AsyncSession, Depends(db_getter)],
        data: Annotated[DocumentBatchAvailableSchema, Body(description="批量上下架参数")],
) -> JSONResponse:
    await IntegrationKbDocumentService(db).batch_set_available(data=data)
    return SuccessResponse(msg="批量设置上下架成功")


@IntegrationKbDocumentRouter.post("/reparse/{id}", summary="06-重新解析知识文档", response_model=ResponseSchema[TaskOutSchema])
async def reparse_document_controller(
        db: Annotated[AsyncSession, Depends(db_getter)],
        id: Annotated[int, Path(description="文档ID", ge=1)],
) -> JSONResponse:
    result_dict = await IntegrationKbDocumentService(db).reparse(id=id)
    return SuccessResponse(data=result_dict, msg="已提交重新解析")


@IntegrationKbDocumentRouter.delete("/delete", summary="07-删除知识文档", response_model=ResponseSchema[None])
async def delete_document_controller(
        db: Annotated[AsyncSession, Depends(db_getter)],
        ids: Annotated[list[int], Body(description="文档ID列表")],
) -> JSONResponse:
    await IntegrationKbDocumentService(db).delete(ids=ids)
    return SuccessResponse(msg="删除知识文档成功（已级联清理分片与解析产物）")


@IntegrationKbDocumentRouter.get("/preview/{id}", summary="08-预览知识文档", response_model=ResponseSchema[DocumentPreviewOutSchema])
async def preview_document_controller(
        db: Annotated[AsyncSession, Depends(db_getter)],
        id: Annotated[int, Path(description="文档ID", ge=1)],
) -> JSONResponse:
    result_dict = await IntegrationKbDocumentService(db).preview(id=id)
    return SuccessResponse(data=result_dict, msg="获取文档预览成功")


@IntegrationKbDocumentRouter.get("/source/{id}", summary="09-读取知识文档原件（pdf / 图片 / 纯文本内联预览）")
async def get_document_source_controller(
        db: Annotated[AsyncSession, Depends(db_getter)],
        id: Annotated[int, Path(description="文档ID", ge=1)],
        download: Annotated[bool, Query(description="是否作为附件下载")] = False,
) -> Response:
    data, media_type, file_name = await IntegrationKbDocumentService(db).source(id=id)
    # 预览内联下发（iframe / <img> 直接消费）；下载按 RFC 5987 补附件名
    headers = _attachment_header(file_name) if download else {
        "Cache-Control": "private, max-age=60",
        "X-Content-Type-Options": "nosniff",
    }
    return Response(content=data, media_type=media_type, headers=headers)


@IntegrationKbDocumentRouter.get("/image/{id}/{name}", summary="10-读取解析产物内嵌图片")
async def get_document_image_controller(
        db: Annotated[AsyncSession, Depends(db_getter)],
        id: Annotated[int, Path(description="文档ID", ge=1)],
        name: Annotated[str, Path(description="图片文件名")],
) -> Response:
    data, media_type, _ = await IntegrationKbDocumentService(db).image(id=id, name=name)
    return Response(content=data, media_type=media_type, headers={"Cache-Control": "public, max-age=86400"})


@IntegrationKbDocumentRouter.post("/download", summary="11-批量下载知识文档产物")
async def download_document_controller(
        db: Annotated[AsyncSession, Depends(db_getter)],
        data: Annotated[DocumentDownloadSchema, Body(description="下载参数")],
) -> Response:
    content, file_name = await IntegrationKbDocumentService(db).download(data=data)
    return Response(content=content, media_type="application/zip", headers=_attachment_header(file_name))
