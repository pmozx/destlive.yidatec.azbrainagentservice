"""文件存储接口（/system/storage/*）：浏览、上传与删除对象文件。

六个接口都由页面直接消费：
- ``config``：配置回显（未启用也返回 200，页面据此显示降级提示，而不是弹一个 503）；
- ``objects``：按目录列文件夹与文件，续传令牌分页；
- ``presign``：签发下载链接（大文件走这条，浏览器直连对象存储）；
- ``content``：代理读取内容，用于预览/小文件下载——桶是内网地址时浏览器到不了，
  且预签名 URL 在部分私有化环境同样不可达，所以保留这条兜底通道；
- ``upload``：multipart 上传单文件，同名默认 409、超限 413；
- ``objects``（DELETE）：批量删除文件，目录不支持删除。

删除接口动的是桶内真实对象，而这些对象往往被库里 ``object_key`` 引用（如解析页图），
页面侧必须把风险写进确认框；后端只负责键校验与逐个执行。
"""

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Body, File, Form, Query, Response, UploadFile
from fastapi.responses import JSONResponse

from app.common.response import ResponseSchema, SuccessResponse
from app.core.router_class import ApiLogRoute

from .schema import (
    StorageBrowseOutSchema,
    StorageConfigOutSchema,
    StorageDeleteInSchema,
    StorageDeleteOutSchema,
    StoragePresignOutSchema,
    StorageUploadOutSchema,
)
from .service import MAX_PAGE_SIZE, StorageService

StorageRouter = APIRouter(route_class=ApiLogRoute, prefix="/storage", tags=["103-文件存储"])


@StorageRouter.get("/config", summary="01-对象存储配置回显", response_model=ResponseSchema[StorageConfigOutSchema])
async def get_storage_config_controller() -> JSONResponse:
    result_dict = StorageService().config()
    return SuccessResponse(data=result_dict, msg="获取对象存储配置成功")


@StorageRouter.get(
    "/objects", summary="02-按目录列出文件夹与文件", response_model=ResponseSchema[StorageBrowseOutSchema]
)
async def get_storage_objects_controller(
    prefix: Annotated[str, Query(max_length=512, description="目录前缀（含尾斜杠），留空为根目录")] = "",
    keyword: Annotated[str | None, Query(max_length=128, description="名称过滤词，拼到目录前缀后做前缀匹配")] = None,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE, description="单页数量（S3 单次上限 1000）")] = 100,
    continuation_token: Annotated[str | None, Query(description="上一页返回的 next_token")] = None,
) -> JSONResponse:
    result_dict = await StorageService().browse(
        prefix=prefix,
        keyword=keyword,
        page_size=page_size,
        continuation_token=continuation_token,
    )
    return SuccessResponse(data=result_dict, msg="查询对象列表成功")


@StorageRouter.get(
    "/presign", summary="03-签发对象预签名下载链接", response_model=ResponseSchema[StoragePresignOutSchema]
)
async def get_storage_presign_controller(
    key: Annotated[str, Query(min_length=1, max_length=1024, description="对象键（不含全局前缀）")],
) -> JSONResponse:
    result_dict = StorageService().presign(key=key)
    return SuccessResponse(data=result_dict, msg="生成下载链接成功")


@StorageRouter.get("/content", summary="04-代理读取对象内容（预览/下载）")
async def get_storage_content_controller(
    key: Annotated[str, Query(min_length=1, max_length=1024, description="对象键（不含全局前缀）")],
    download: Annotated[bool, Query(description="true=作为附件下载；false=交给浏览器内联预览")] = False,
) -> Response:
    content, media_type, filename = await StorageService().read(key=key)
    disposition = "attachment" if download else "inline"
    return Response(
        content=content,
        media_type=media_type,
        headers={
            # filename* 走 RFC 5987，避免中文文件名在 Content-Disposition 里被截断
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(filename)}",
            "Content-Length": str(len(content)),
        },
    )


@StorageRouter.post(
    "/upload", summary="05-上传对象文件", response_model=ResponseSchema[StorageUploadOutSchema]
)
async def upload_storage_object_controller(
    file: Annotated[UploadFile, File(description="待上传文件，单文件上限见 STORAGE_UPLOAD_MAX_SIZE_MB")],
    prefix: Annotated[str, Form(max_length=512, description="目标目录前缀（含尾斜杠），留空为根目录")] = "",
    overwrite: Annotated[bool, Form(description="同名是否覆盖；false 时同名返回 409 由页面确认")] = False,
) -> JSONResponse:
    result_dict = await StorageService().upload(file, prefix=prefix, overwrite=overwrite)
    msg = "覆盖上传对象成功" if result_dict.overwritten else "上传对象成功"
    return SuccessResponse(data=result_dict, msg=msg)


@StorageRouter.delete(
    "/objects", summary="06-批量删除对象文件", response_model=ResponseSchema[StorageDeleteOutSchema]
)
async def delete_storage_objects_controller(
    data: Annotated[StorageDeleteInSchema, Body(description="待删除对象键列表（仅文件，不支持目录）")],
) -> JSONResponse:
    result_dict = await StorageService().delete(data.keys)
    return SuccessResponse(data=result_dict, msg=f"已删除 {len(result_dict.deleted)} 个对象")
