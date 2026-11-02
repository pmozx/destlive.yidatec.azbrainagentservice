"""`/v1` 解析链路（#01 / #02）的服务层：类型/大小校验 → 拉取 → 后台解析 → 响应投影。

分工：
- 字段校验（必填 / FAQ 条件必填 / 枚举）在 ``schema.py``；
- 类型白名单、10MB 上限、文件拉取与任务编排在本层；
- 任务状态机与产物落库在 ``app.modules.kb.parse_job.service``；
- 解析/分片复用 ``app.core.rag``（解析 → 分片 → 向量化唯一实现），不在本层另写一套解析器。

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

from app.core.clients.download import DOWNLOAD_TIMEOUT_SECONDS, DownloadError, download_bytes
from app.core.exceptions import CustomException
from app.core.logger import logger
from app.core.rag import ChunkedDocument, parse_and_chunk, supported_suffixes
from app.modules.kb.parse_job.service import ParseJobSnapshot, KbParseJobService
from app.utils.page_locator import PageLocator

from ..errors import DEFAULT_MESSAGES, INVALID_REQUEST, PARSE_FAILED, PAYLOAD_TOO_LARGE, ContractApiError
from .schema import ParseJobRequest

# 契约上限：单文件 10MB（正文里声明的 file_size 与实际拉取的字节都要校验）
MAX_FILE_BYTES: int = 10 * 1024 * 1024

PARSE_JOB_SERVICE = KbParseJobService()


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
    """流式拉取 `file_url` 并在超限时立刻中断（下载实现与单例在 ``app.core.clients.download``）。"""
    try:
        return await download_bytes(url, max_bytes=max_bytes, timeout=DOWNLOAD_TIMEOUT_SECONDS)
    except DownloadError as exc:
        code = PAYLOAD_TOO_LARGE if exc.too_large else INVALID_REQUEST
        raise ContractApiError(code, str(exc)) from exc


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
