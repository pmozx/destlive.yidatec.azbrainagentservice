"""解析任务管理服务：列表 / 详情（只读） + 产物切片编辑 + 管理端自助提交解析。

管理端提交与契约 ``#01`` 走**同一条流水线**（``parse_job.pipeline.run_parse_job``）：
文件来源从 ``file_url`` 换成上传，但解析、切片、页图、建议完全一致——否则同一个文件
经两个入口会解析出不同切片。

切片编辑只动 ``parse_result.chunks[seq]`` 这一条：产物是本侧唯一快照，不改任务状态机，
也不重算 ``content_hash``（重算需要整篇原文，产物里已不存在），靠 ``chunks[].edited`` 区分。

``publish_chunks`` 把产物里的切片集合（含人工修订）直接写成一版正式知识（契约 03.1
等价）：切片不再交给 ``split_text`` 重切，否则会改掉边界与修订内容；防重用产物的
``publication_id`` 列——写完即可看到「已写入」，再次提交直接 409。

错误口径：领域层只抛 ``ParseInvalidRequestError`` / ``ParseFileTooLargeError``，
本层翻成管理端 ``CustomException``（400 / 413），不把 HTTP 语义带进领域。
"""

import asyncio
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.setting import settings
from app.core.base_schema import PageResultSchema
from app.core.clients.s3 import get_s3_client
from app.core.exceptions import CustomException
from app.core.logger import logger
from app.utils.common_util import search_to_dict

from ..parse_result.crud import KbParseResultCRUD
from ..parse_result.preview import refresh_pages
from ..publication.schema import KbPublicationEntryChunkSchema, KbPublicationEntryChunksWriteSchema
from ..publication.service import KbPublicationService
from .artifacts import presign_artifacts
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
    KbParseChunkOutSchema,
    KbParseChunkUpdateSchema,
    KbParseJobDetailOutSchema,
    KbParseJobOutSchema,
    KbParseJobQueryParam,
    KbParsePublishOutSchema,
    KbParsePublishSchema,
    KbParseResultOutSchema,
    KbParseStepOutSchema,
)
from .step import STEP_FAILED, KbParseStepService

STEP_SERVICE = KbParseStepService()

#: 上传原件的对象键前缀：与 ``parse-previews`` 隔离，便于单独配生命周期策略
SOURCE_KEY_PREFIX = "parse-sources"

#: 文件名里只保留安全字符：中文与路径分隔符进对象键会让某些网关签名出错
_UNSAFE_KEY = re.compile(r"[^A-Za-z0-9._-]")

#: 后台任务失败码：与契约 ``#02`` 同一落库口径（取值相同），页面文案由管理端自定
PARSE_FAILED = "PARSE_FAILED"
PARSE_FAILED_MESSAGE = "文档解析失败，请检查文件内容或联系管理员。"

#: 卡死改判码：步骤长时间无进展（进程还在但连接半死）时由惰性看门狗写入
PARSE_STALLED = "PARSE_STALLED"


def _int_of(value: Any) -> int | None:
    """JSONB 里的整数（seq / 页号）：可能是数字字符串，脏值当「缺失」处理。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _seq_of(chunk: dict[str, Any]) -> int | None:
    """取产物切片的 seq：理论上都是整数，脏数据当「不可比」处理（返回 None 即不命中）。"""
    return _int_of(chunk.get("seq"))


def _page_of(value: Any) -> int | None:
    """页号：负数/脏值当缺失（发布单元侧只收非负整数）。"""
    number = _int_of(value)
    return number if number is not None and number >= 0 else None


@dataclass(frozen=True)
class ParseSubmitTask:
    """一次提交的「待执行」参数（响应后才跑，不随请求事务存活）。

    ``data`` 为空=重跑：流水线按 ``object_key`` → ``file_url`` 自己重新取原件。
    """

    job_id: str
    file_name: str
    data: bytes | None
    chunk_enabled: bool
    resource_type: str
    prompt_text: str | None
    model_key: str | None
    metadata: dict[str, Any]
    language: str
    attempt: int = 1
    from_step: str | None = None
    object_key: str | None = None
    file_url: str | None = None


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
        attempt=task.attempt,
        from_step=task.from_step,
        object_key=task.object_key,
        file_url=task.file_url,
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
        """任务详情：任务行 + 步骤台账 + （若成功）解析产物与产物下载地址。"""
        await self._reap_stalled(job_id)
        job = await self.crud.get_or_404(uuid=job_id, msg="解析任务不存在")
        result = await KbParseResultCRUD(self.db).get(job_id=job_id)
        result_out = KbParseResultOutSchema.model_validate(result) if result else None
        artifact_urls: dict[str, Any] | None = None
        if result_out is not None:
            # 页图与产物都是对象存储的预签名 URL，按当前配置重签，避免历史任务回放过期链接
            result_out.pages = refresh_pages(result_out.pages)
            artifact_urls = presign_artifacts(result_out.artifacts)
        return KbParseJobDetailOutSchema(
            id=job.id,
            uuid=job.uuid,
            created_time=job.created_time,
            updated_time=job.updated_time,
            resource_type=job.resource_type,
            status=job.status,
            progress=job.progress,
            attempt=job.attempt,
            error_code=job.error_code,
            error_message=job.error_message,
            file_meta=job.file_meta or {},
            result=result_out,
            steps=await self.list_steps(job_id),
            artifact_urls=artifact_urls,
        )

    async def list_steps(self, job_id: str) -> list[KbParseStepOutSchema]:
        """步骤台账（含历史执行轮次，按 attempt/id 升序）；前端据此渲染时间线。"""
        rows = await STEP_SERVICE.list_steps(job_id)
        return [KbParseStepOutSchema.model_validate(row) for row in rows]

    async def retry(self, job_id: str, *, from_step: str | None = None) -> tuple[KbParseJobOutSchema, ParseSubmitTask]:
        """重跑：把失败/卡死的任务重新排队，从失败那一步继续（响应后执行）。

        只接受 failed / running；succeeded 只在 ``from_step=chunk`` 时放行（换切块口径
        重算切片，不重新解析），其余情况一律拒掉。起点由流水线自己再往前回退到
        「前序快照齐全」的位置，这里只做透传。
        """
        snapshot, reason = await PARSE_JOB_SERVICE.requeue(job_id, from_step=from_step)
        if snapshot is None:
            raise CustomException(msg=reason or "解析任务不存在", status_code=status.HTTP_404_NOT_FOUND)
        if reason:
            raise CustomException(msg=reason, status_code=status.HTTP_409_CONFLICT)
        job = await self.crud.get_or_404(uuid=job_id, msg="解析任务重新排队后读取失败")
        out = KbParseJobOutSchema(
            id=job.id,
            uuid=job.uuid,
            created_time=job.created_time,
            updated_time=job.updated_time,
            resource_type=job.resource_type,
            status=job.status,
            progress=job.progress,
            attempt=job.attempt,
            error_code=job.error_code,
            error_message=job.error_message,
        )
        return out, self._retry_task(job, from_step=from_step)

    async def _reap_stalled(self, job_id: str) -> None:
        """惰性看门狗：把「running 但当前步骤已远超预算」的任务改判失败。

        为什么不做定时任务：卡死只在有人看的时候才需要一个结论。读取时判一次，
        既不引入调度线程，也不会让「进程还活着但连接半死」的任务永远显示运行中。
        改判只动状态不改 ``attempt``——旧协程真的跑完了还能把结果覆盖回来（产物有效）。
        """
        job = await self.crud.get(uuid=job_id)
        if job is None or job.status != "running":
            return
        limit = settings.PARSE_STEP_STALL_SECONDS
        stalled = await STEP_SERVICE.stalled_step(job_id, timeout_seconds=limit)
        if stalled is None:
            return
        minutes = limit / 60
        reason = f"步骤「{stalled.step}」超过 {minutes:.0f} 分钟无任何进展，已判定为卡死。"
        logger.warning("解析任务步骤卡死，改判失败：job_id={} step={} attempt={}", job_id, stalled.step, stalled.attempt)
        await STEP_SERVICE.finish(stalled.id, status=STEP_FAILED, error_code="STEP_STALLED", error_message=reason)
        await PARSE_JOB_SERVICE.fail(job_id, error_code=PARSE_STALLED, error_message=reason, attempt=job.attempt)

    def _retry_task(self, job: Any, *, from_step: str | None) -> ParseSubmitTask:
        """从任务快照还原重跑参数：原件走对象存储，缺了就退回当初的 ``file_url``。"""
        meta = dict(job.file_meta or {})
        file_name = str(meta.get("file_name") or "").strip()
        if not file_name:
            raise CustomException(msg="任务快照里没有文件名，无法重跑。", status_code=status.HTTP_400_BAD_REQUEST)
        options = meta.get("options") if isinstance(meta.get("options"), dict) else {}
        return ParseSubmitTask(
            job_id=job.uuid,
            file_name=file_name,
            data=None,
            chunk_enabled=bool(options.get("chunk", True)),
            resource_type=job.resource_type,
            prompt_text=meta.get("prompt_text"),
            model_key=meta.get("model_key"),
            metadata=self._retry_metadata(meta),
            language=str(options.get("language") or "zh-CN"),
            attempt=job.attempt,
            from_step=from_step,
            object_key=str(meta.get("object_key") or "") or None,
            file_url=str(meta.get("file_url") or "") or None,
        )

    @staticmethod
    def _retry_metadata(meta: dict[str, Any]) -> dict[str, Any]:
        """续跑用的 metadata：契约任务取 payload 里的 ``metadata``，管理端任务取整份快照。"""
        inner = meta.get("metadata")
        if isinstance(inner, dict) and inner:
            return dict(inner)
        return dict(meta)

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

    async def update_chunk(self, job_id: str, data: KbParseChunkUpdateSchema) -> KbParseChunkOutSchema:
        """按 ``seq`` 覆盖单条切片的正文（可选改标题），不动其余切片与任务状态。

        不重算 ``content_hash``：它代表整篇解析文本，切片改过之后与原文不再一致；
        因此改过的切片打 ``edited`` 标记，让页面与调用方能区分人工修正。
        """
        await self.crud.get_or_404(uuid=job_id, msg="解析任务不存在")
        crud = KbParseResultCRUD(self.db)
        result = await crud.get(job_id=job_id)
        if result is None:
            raise CustomException(msg="该任务还没有解析产物，无法编辑切片", status_code=status.HTTP_404_NOT_FOUND)

        # JSONB 数组不在 ORM 变更追踪内：整列重新赋值才会生成 UPDATE
        chunks = [dict(item) for item in (result.chunks or []) if isinstance(item, dict)]
        index = next((i for i, item in enumerate(chunks) if _seq_of(item) == data.seq), None)
        if index is None:
            raise CustomException(msg=f"切片 seq={data.seq} 不存在", status_code=status.HTTP_404_NOT_FOUND)

        chunk = chunks[index]
        chunk["content"] = data.content
        if data.heading is not None:
            heading = data.heading.strip()
            if heading:
                chunk["heading"] = heading
            else:
                chunk.pop("heading", None)
        chunk["edited"] = True
        chunk["edited_time"] = datetime.now(UTC).isoformat(timespec="seconds")

        await crud.update(result.id, {"chunks": chunks})
        logger.info("解析产物切片已人工编辑：job_id={} seq={} 字数={}", job_id, data.seq, len(data.content))
        return KbParseChunkOutSchema(
            seq=data.seq,
            heading=chunk.get("heading"),
            content=data.content,
            edited=True,
            edited_time=chunk["edited_time"],
        )

    async def publish_chunks(self, job_id: str, data: KbParsePublishSchema) -> KbParsePublishOutSchema:
        """把解析产物写成一份正式知识（03.1 等价）：切片原样入库，不再二次切分。

        产物里的切片已逐条审过（可含人工修订），所以这里不接正文、也不调 ``split_text``；
        缺省的展示字段从任务快照推导（标题取文件名、租户取契约固定值、资源 ID 取 ``doc_id``）。
        防重：产物已有 ``publication_id`` 就拒绍——同一产物写两次会产生两个发布单元，
        而对外口径是「一份解析产物 = 一份知识」；要改内容请到发布单元页修订。
        """
        job = await self.crud.get_or_404(uuid=job_id, msg="解析任务不存在")
        crud = KbParseResultCRUD(self.db)
        result = await crud.get(job_id=job_id)
        if result is None:
            raise CustomException(msg="该任务还没有解析产物，无法写入知识平台", status_code=status.HTTP_404_NOT_FOUND)
        if result.publication_id:
            raise CustomException(
                msg=f"该解析产物已写入发布单元 {result.publication_id}，如需调整请到发布单元页修订",
                status_code=status.HTTP_409_CONFLICT,
            )

        chunks = self._publishable_chunks(result.chunks or [])
        if not chunks:
            raise CustomException(msg="解析产物里没有可写入的正文切片", status_code=status.HTTP_400_BAD_REQUEST)

        file_meta = job.file_meta or {}
        file_name = str(file_meta.get("file_name") or "")
        metadata: dict[str, Any] = {"parse_job_id": job_id}
        if file_name:
            metadata["file_name"] = file_name
        receipt = await KbPublicationService(self.db).write_entry_from_chunks(
            KbPublicationEntryChunksWriteSchema(
                publication_id=data.publication_id,
                resource_type=data.resource_type or job.resource_type or "document",
                # 租户必须落到契约固定值：检索按 publication.tenant_id 硬过滤，留空会检索不到
                tenant_id=data.tenant_id or file_meta.get("tenant_id") or settings.CONTRACT_TENANT_ID,
                resource_id=data.resource_id or file_meta.get("doc_id"),
                title=(data.title or "").strip() or self._default_title(job_id, file_name),
                category=data.category,
                summary=data.summary,
                tags=data.tags,
                owner=data.owner,
                knowledge_no=data.knowledge_no,
                metadata=metadata,
                chunks=chunks,
            )
        )

        # 回执写回产物行：它既是页面上的「已写入」标识，也是防止重复发布的唯一依据
        published_time = datetime.now(UTC)
        await crud.update(result.id, {"publication_id": receipt.publication_id, "published_time": published_time})
        logger.info(
            "解析产物已写入知识平台：job_id={} publication_id={} 切片数={}",
            job_id,
            receipt.publication_id,
            receipt.chunk_count,
        )
        return KbParsePublishOutSchema(
            job_id=job_id,
            publication_id=receipt.publication_id,
            record_id=receipt.record_id,
            version_id=receipt.version_id,
            revision=receipt.revision,
            content_hash=receipt.content_hash,
            chunk_count=receipt.chunk_count,
            searchable=receipt.searchable,
            status=receipt.status,
            published_time=published_time,
        )

    @staticmethod
    def _publishable_chunks(raw: list[Any]) -> list[KbPublicationEntryChunkSchema]:
        """产物切片 → 待写入切片：丢掉空正文 / 无 seq 的脏数据，按 seq 升序。

        ``seq`` 只用于排序，重编号 1..N 由发布单元侧统一做（契约要求 seq 从 1 连续，
        而产物可能因人工删改出现断号）。
        """
        chunks: list[KbPublicationEntryChunkSchema] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            seq = _seq_of(item)
            content = str(item.get("content") or "").strip()
            if seq is None or not content:
                continue
            chunks.append(
                KbPublicationEntryChunkSchema(
                    seq=seq,
                    content=content,
                    heading=str(item.get("heading") or "").strip() or None,
                    page_from=_page_of(item.get("page_from")),
                    page_to=_page_of(item.get("page_to")),
                )
            )
        chunks.sort(key=lambda chunk: chunk.seq)
        return chunks

    @staticmethod
    def _default_title(job_id: str, file_name: str) -> str:
        """标题缺省：原文件名去扩展名（超长截断到 schema 上限）；仍为空时用任务号兜底。"""
        return Path(file_name).stem.strip()[:200] or f"解析产物-{job_id[:8]}"

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
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
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
            return CustomException(msg=str(exc), status_code=status.HTTP_413_CONTENT_TOO_LARGE)
        return CustomException(msg=str(exc))
