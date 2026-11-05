"""解析任务管理服务：列表 / 详情（只读） + 管理端自助提交解析（上传 → 建任务 → 落原件）。

管理端提交与契约 ``#01`` 走**同一条流水线**（``parse_job.pipeline.run_parse_job``）：
文件来源从 ``file_url`` 换成上传，但解析、切片、页图、建议完全一致——否则同一个文件
经两个入口会解析出不同切片。

错误口径：领域层只抛 ``ParseInvalidRequestError`` / ``ParseFileTooLargeError``，
本层翻成管理端 ``CustomException``（400 / 413），不把 HTTP 语义带进领域。
"""

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import UploadFile, status as http_status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.setting import settings
from app.core.base_schema import PageResultSchema
from app.core.clients.s3 import get_s3_client
from app.core.exceptions import CustomException
from app.core.logger import logger
from app.utils.common_util import search_to_dict

from ..parse_result.crud import KbParseResultCRUD
from ..parse_result.preview import refresh_pages
from .crud import KbParseJobCRUD
from .pipeline import (
    PARSE_JOB_SERVICE,
    ParseFileTooLargeError,
    ParseInvalidRequestError,
    max_file_bytes,
    run_parse_job,
    validate_file_request,
)
from .schema import (
    KbParseJobDetailOutSchema,
    KbParseJobOutSchema,
    KbParseJobQueryParam,
    KbParseResultOutSchema,
)

#: 上传原件的对象键前缀：与 ``parse-previews`` 隔离，便于单独配生命周期策略
SOURCE_KEY_PREFIX = "parse-sources"

#: 文件名里只保留安全字符：中文与路径分隔符进对象键会让某些网关签名出错
_UNSAFE_KEY = re.compile(r"[^A-Za-z0-9._-]")

#: 后台任务失败码：与契约 ``#02`` 同一落库口径（取值相同），页面文案由管理端自定
PARSE_FAILED = "PARSE_FAILED"
PARSE_FAILED_MESSAGE = "文档解析失败，请检查文件内容或联系管理员。"


@dataclass(frozen=True)
class ParseSubmitTask:
    """一次管理端提交的「待执行」参数（响应后才跑，不随请求事务存活）。"""

    job_id: str
    file_name: str
    data: bytes
    chunk_enabled: bool
    resource_type: str
    prompt_text: str | None
    model_key: str | None
    metadata: dict[str, Any]
    language: str


async def run_submitted_parse(task: ParseSubmitTask) -> None:
    """后台任务入口：把提交参数转成流水线关键字参数（签名只在这两处对齐一次）。"""
    await run_parse_job(
        job_id=task.job_id,
        file_name=task.file_name,
        data=task.data,
        chunk_enabled=task.chunk_enabled,
        resource_type=task.resource_type,
        prompt_text=task.prompt_text,
        model_key=task.model_key,
        metadata=task.metadata,
        language=task.language,
        failure_code=PARSE_FAILED,
        failure_message=PARSE_FAILED_MESSAGE,
    )


class KbParseJobAdminService:
    """解析任务管理服务（复用请求级会话；只读查询不写状态）。"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.crud = KbParseJobCRUD(db)

    async def page(
        self,
        *,
        page_no: int,
        page_size: int,
        search: KbParseJobQueryParam | None = None,
        order_by: list[dict[str, str]] | None = None,
    ) -> PageResultSchema[KbParseJobOutSchema]:
        """分页查询解析任务：默认按创建时间倒序（最新任务在最前）。"""
        return await self.crud.page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=order_by or [{"created_time": "desc"}],
            search=search_to_dict(search),
            out_schema=KbParseJobOutSchema,
        )

    async def detail(self, job_id: str) -> KbParseJobDetailOutSchema:
        """任务详情：任务行 + （若成功）解析产物。"""
        job = await self.crud.get_or_404(uuid=job_id, msg="解析任务不存在")
        result = await KbParseResultCRUD(self.db).get(job_id=job_id)
        result_out = KbParseResultOutSchema.model_validate(result) if result else None
        if result_out is not None:
            # 页图是对象存储的预签名 URL，按当前配置重签，避免历史任务回放过期链接
            result_out.pages = refresh_pages(result_out.pages)
        out = KbParseJobDetailOutSchema(
            id=job.id,
            uuid=job.uuid,
            created_time=job.created_time,
            updated_time=job.updated_time,
            resource_type=job.resource_type,
            status=job.status,
            progress=job.progress,
            error_code=job.error_code,
            error_message=job.error_message,
            file_meta=job.file_meta or {},
            result=result_out,
        )
        return out

    async def submit_upload(
        self,
        upload: UploadFile,
        *,
        resource_type: str,
        chunk_enabled: bool,
        language: str,
        prompt_text: str | None,
        model_key: str | None,
    ) -> tuple[KbParseJobOutSchema, ParseSubmitTask | None]:
        """上传并建任务：返回 ``(任务视图, 待执行参数)``，``None`` 表示任务行已存在不重跑。

        校验顺序与 ``#01`` 一致：先看类型/大小（不产生任务行），再落原件，最后建任务。
        原件留存失败只告警不阻断：解析用的是内存里的字节，不依赖对象存储。
        """
        file_name = upload.filename or "upload"
        data = await self._read_limited(upload)
        try:
            validate_file_request(file_name, len(data))
        except (ParseInvalidRequestError, ParseFileTooLargeError) as exc:
            raise self._map_error(exc) from exc

        job_id = uuid4().hex
        object_key = await self._store_source(job_id, file_name, data)
        metadata = {
            "file_name": file_name,
            "file_size": len(data),
            "resource_type": resource_type,
            "options": {"chunk": chunk_enabled, "language": language},
            "prompt_text": prompt_text,
            "model_key": model_key,
            "object_key": object_key,
            "submitted_by": "admin",
        }
        _, created = await PARSE_JOB_SERVICE.create_job(
            job_id=job_id, resource_type=resource_type, file_meta=metadata
        )
        job = await self.crud.get_or_404(uuid=job_id, msg="解析任务创建后读取失败")
        out = KbParseJobOutSchema(
            id=job.id,
            uuid=job.uuid,
            created_time=job.created_time,
            updated_time=job.updated_time,
            resource_type=job.resource_type,
            status=job.status,
            progress=job.progress,
            error_code=job.error_code,
            error_message=job.error_message,
        )
        if not created:
            return out, None
        return out, ParseSubmitTask(
            job_id=job_id,
            file_name=file_name,
            data=data,
            chunk_enabled=chunk_enabled,
            resource_type=resource_type,
            prompt_text=prompt_text,
            model_key=model_key,
            metadata=metadata,
            language=language,
        )

    @staticmethod
    async def _read_limited(upload: UploadFile) -> bytes:
        """分块读上传内容并在超限时立刻中断（不把整个超大文件读进内存再判）。"""
        limit = max_file_bytes()
        buffer = bytearray()
        while True:
            chunk = await upload.read(64 * 1024)
            if not chunk:
                break
            buffer.extend(chunk)
            if len(buffer) > limit:
                raise CustomException(
                    msg=f"文件超过 {limit // (1024 * 1024)}MB 上限。",
                    status_code=http_status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                )
        if not buffer:
            raise CustomException(msg="上传文件为空。")
        return bytes(buffer)

    @staticmethod
    async def _store_source(job_id: str, file_name: str, data: bytes) -> str | None:
        """把原件存进对象存储（与解析产物隔离的前缀）；未启用或失败时返回 ``None``。"""
        if not settings.S3_ENABLE:
            return None
        suffix = Path(file_name).suffix.lower()
        key = f"{SOURCE_KEY_PREFIX}/{_UNSAFE_KEY.sub('_', job_id)}/source{suffix}"
        try:
            return await asyncio.to_thread(get_s3_client().upload_bytes, data, key)
        except Exception as exc:  # noqa: BLE001 原件留存不影响解析
            logger.warning("解析原件保存失败（job_id={}）：{}", job_id, exc)
            return None

    @staticmethod
    def _map_error(exc: ParseInvalidRequestError | ParseFileTooLargeError) -> CustomException:
        """领域异常 → 管理端状态码：超限 413、其余请求问题 400。"""
        if isinstance(exc, ParseFileTooLargeError):
            return CustomException(msg=str(exc), status_code=http_status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
        return CustomException(msg=str(exc))
