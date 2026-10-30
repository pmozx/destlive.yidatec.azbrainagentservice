"""`/v1` 解析链路（#01 / #02）的契约适配层：校验 → 拉取 → 后台解析 → 响应投影。

分工：
- 契约字段校验（必填 / FAQ 条件必填 / 类型白名单 / 10MB）在这里；
- 任务状态机与产物落库在 ``app.modules.kb.parse_job.service``；
- 解析/分片复用 ``integration_kb.pipeline``，不在本层另写一套解析器。

关键口径：
1. ``file_url`` 必须在校验期**同步拉完**再返回 202：超限要能回 413、类型不支持和地址不可读要能回 400，
   把文件留到后台任务再拉，调用方只会看到一个永远 queued 的任务；
2. 202 前必须先把任务行提交：FastAPI 的响应后置任务与依赖退出都在响应发出之后才执行，
   未提交的行后台读不到（POC 期踩过的坑）；
3. 解析失败属于**任务结果**：``#02`` 仍返回 HTTP 200 + ``error_code=PARSE_FAILED``，
   不能把异常抛给 #01 的调用方（那时 202 早已返回）。
"""

import hashlib
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.clients.base import trust_env_for_url
from app.core.exceptions import CustomException
from app.core.logger import logger
from app.modules.integration_kb.pipeline.document_pipeline import ChunkedDocument, parse_and_chunk
from app.modules.integration_kb.pipeline.page_map import PageLocator
from app.modules.integration_kb.pipeline.parsers import supported_suffixes
from app.modules.kb.parse_job.service import ParseJobSnapshot, KbParseJobService

from .errors import (
    DEFAULT_MESSAGES,
    INVALID_REQUEST,
    PARSE_FAILED,
    PAYLOAD_TOO_LARGE,
    ContractApiError,
)

# 契约上限：单文件 10MB（正文里声明的 file_size 与实际拉取的字节都要校验）
MAX_FILE_BYTES: int = 10 * 1024 * 1024
DOWNLOAD_TIMEOUT_SECONDS: float = 30.0

PARSE_JOB_SERVICE = KbParseJobService()


def _require_non_blank(value: str) -> str:
    if not value or not value.strip():
        raise ValueError("不能为空或仅包含空白字符")
    return value


class ParseOptions(BaseModel):
    """`options`：当前 GSI 固定传 ``chunk=true``。"""

    chunk: bool = Field(description="是否生成切片")
    language: str = Field(description="解析语言，与 metadata.language 保持一致")

    @field_validator("language")
    @classmethod
    def _language_required(cls, value: str) -> str:
        return _require_non_blank(value)


class ParseJobRequest(BaseModel):
    """`#01` 请求正文（手册 §六.01 字段逐条对应）。"""

    model_config = ConfigDict(extra="ignore")

    job_id: str = Field(description="GSI 预先生成的解析任务 ID，也是幂等标识")
    doc_id: str = Field(description="文档 / 本次解析所属业务资源 ID")
    version_id: str = Field(description="业务版本标识（字符串，不按数字比较）")
    file_url: str = Field(description="AI 可直接读取的文件 URL（http/https）")
    file_name: str = Field(description="原始文件名，带扩展名")
    options: ParseOptions
    tenant_id: str = Field(description="租户标识，当前固定 gsi-default")
    resource_type: str = Field(description="document / faq")
    metadata: dict[str, Any] = Field(description="页面已保存的元数据快照（解析上下文）")
    file_id: str | None = Field(default=None, description="GSI 文件标识（仅追踪用）")
    file_size: int | None = Field(default=None, ge=0, description="文件大小（字节）")
    prompt_text: str | None = Field(default=None, description="resource_type=faq 时必填")
    model_key: str | None = Field(default=None, description="resource_type=faq 时必填")

    @field_validator("job_id", "doc_id", "version_id", "file_url", "file_name", "tenant_id")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return _require_non_blank(value)

    @field_validator("prompt_text", "model_key")
    @classmethod
    def _optional_text_not_blank(cls, value: str | None) -> str | None:
        return _require_non_blank(value) if value is not None else None

    @field_validator("resource_type")
    @classmethod
    def _resource_type_enum(cls, value: str) -> str:
        if value not in ("document", "faq"):
            raise ValueError("resource_type 只支持 document / faq")
        return value

    @model_validator(mode="after")
    def _faq_requires_prompt_and_model(self) -> "ParseJobRequest":
        if self.resource_type == "faq":
            for name in ("prompt_text", "model_key"):
                value = getattr(self, name)
                if not value or not value.strip():
                    raise ValueError(f"resource_type 为 faq 时，{name} 必填且不能仅包含空白字符。")
        return self


def validate_file_request(payload: ParseJobRequest) -> None:
    """类型白名单与声明大小的前置校验（拉取前失败，不产生任务行）。"""
    suffix = Path(payload.file_name).suffix.lower()
    if suffix not in supported_suffixes():
        raise ContractApiError(
            INVALID_REQUEST,
            f"暂不支持解析 {suffix or '无后缀'} 文件；已接入：{'/'.join(supported_suffixes())}。",
        )
    if payload.file_size is not None and payload.file_size > MAX_FILE_BYTES:
        raise ContractApiError(PAYLOAD_TOO_LARGE, f"文件超过 {MAX_FILE_BYTES // (1024 * 1024)}MB 上限。")


async def fetch_file(url: str, *, max_bytes: int = MAX_FILE_BYTES) -> bytes:
    """流式拉取 `file_url` 并在超限时立刻中断（不把整个超大文件读进内存再判断）。"""
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ContractApiError(INVALID_REQUEST, "file_url 必须为 http/https 地址。")

    try:
        async with httpx.AsyncClient(
            timeout=DOWNLOAD_TIMEOUT_SECONDS,
            follow_redirects=True,
            trust_env=trust_env_for_url(url),
        ) as client:
            async with client.stream("GET", url) as response:
                if response.status_code != httpx.codes.OK:
                    raise ContractApiError(
                        INVALID_REQUEST,
                        f"file_url 读取失败（HTTP {response.status_code}），请确认地址有效。",
                    )
                declared = response.headers.get("content-length", "")
                if declared.isdigit() and int(declared) > max_bytes:
                    raise ContractApiError(
                        PAYLOAD_TOO_LARGE,
                        f"文件超过 {max_bytes // (1024 * 1024)}MB 上限。",
                    )
                buffer = bytearray()
                async for part in response.aiter_bytes():
                    buffer.extend(part)
                    if len(buffer) > max_bytes:
                        raise ContractApiError(
                            PAYLOAD_TOO_LARGE,
                            f"文件超过 {max_bytes // (1024 * 1024)}MB 上限。",
                        )
    except httpx.HTTPError as exc:
        raise ContractApiError(INVALID_REQUEST, "file_url 无法访问，请确认解析服务可访问该地址。") from exc
    return bytes(buffer)


def build_result_chunks(chunked: ChunkedDocument, *, enabled: bool) -> list[dict[str, Any]]:
    """把分片投影成契约结构：``seq`` 从 1 起，页码有版面信息才回填（无则省略）。"""
    if not enabled:
        return []
    locator = PageLocator(chunked.document.page_map)
    rows: list[dict[str, Any]] = []
    for chunk in chunked.chunks:
        item: dict[str, Any] = {
            "seq": chunk.index + 1,
            "content": chunk.content,
            "source_type": "text",
        }
        page_no = locator.locate(chunk.content)
        if page_no is not None:
            item["page_from"] = page_no
            item["page_to"] = page_no
        heading = str(chunk.meta.get("section_title") or "").strip()
        if heading:
            item["heading"] = heading
        rows.append(item)
    return rows


async def create_parse_job_record(payload: ParseJobRequest) -> tuple[ParseJobSnapshot, bool]:
    """把请求快照转成任务行（``#01`` 202 前必须已提交）。"""
    return await PARSE_JOB_SERVICE.create_job(
        job_id=payload.job_id,
        resource_type=payload.resource_type,
        file_meta={
            "file_name": payload.file_name,
            "file_url": payload.file_url,
            "doc_id": payload.doc_id,
            "version_id": payload.version_id,
            "tenant_id": payload.tenant_id,
            "file_id": payload.file_id,
            "file_size": payload.file_size,
            "metadata": payload.metadata,
        },
    )


async def get_parse_job_snapshot(job_id: str) -> ParseJobSnapshot | None:
    """``#02`` 读取任务快照（成功态带产物）。"""
    return await PARSE_JOB_SERVICE.get_snapshot(job_id)


async def run_parse_job(*, job_id: str, file_name: str, data: bytes, chunk_enabled: bool) -> None:
    """响应后置任务：解析 → 分片 → 写产物；任何异常都转成 failed，不向调用方抛。"""
    try:
        await PARSE_JOB_SERVICE.mark_running(job_id)
        chunked = await parse_and_chunk(Path(file_name), data=data)
        await PARSE_JOB_SERVICE.complete(
            job_id,
            chunks=build_result_chunks(chunked, enabled=chunk_enabled),
            suggested=None,
            warnings=[],
            content_hash=hashlib.sha256(chunked.document.text.encode("utf-8")).hexdigest(),
        )
    except Exception as exc:
        message = exc.msg if isinstance(exc, CustomException) else DEFAULT_MESSAGES[PARSE_FAILED]
        logger.exception("解析任务失败（job_id={}）：{}", job_id, exc)
        await PARSE_JOB_SERVICE.fail(job_id, error_code=PARSE_FAILED, error_message=message)


def accepted_response(payload: ParseJobRequest, snapshot: ParseJobSnapshot) -> dict[str, Any]:
    """`#01` 202 受理回执：只回任务 ID 与当前状态，不提前透露结果。"""
    return {
        "job_id": payload.job_id,
        "status": snapshot.status,
        "message": "任务已受理，请查询结果。",
    }


def status_response(snapshot: ParseJobSnapshot) -> dict[str, Any]:
    """`#02` 响应投影：成功带 result，失败带 error_code（HTTP 仍为 200）。"""
    body: dict[str, Any] = {
        "job_id": snapshot.job_id,
        "status": snapshot.status,
        "progress": snapshot.progress,
    }
    if snapshot.status == "succeeded":
        result: dict[str, Any] = {
            "pages": [],
            "chunks": snapshot.chunks or [],
            "warnings": snapshot.warnings or [],
        }
        file_name = str(snapshot.file_meta.get("file_name") or "").strip()
        if file_name:
            result["file_name"] = file_name
        if snapshot.suggested:
            result["suggested"] = snapshot.suggested
        body["result"] = result
    if snapshot.status == "failed":
        body["error_code"] = snapshot.error_code or PARSE_FAILED
        body["error_message"] = snapshot.error_message or DEFAULT_MESSAGES[PARSE_FAILED]
    return body
