"""`/v1` 解析链路（#01 / #02）的接入层：请求投影 → 领域流水线 → 响应投影。

分工：
- 字段校验（必填 / FAQ 条件必填 / 枚举）在 ``schema.py``；
- 流水线（类型白名单、容量上限、拉取、解析、切片/FAQ、页图、建议、进度与产物回写）在
  ``app.modules.kb.parse_job.pipeline``；
- 任务状态机与落库在 ``app.modules.kb.parse_job.service``；
- 本层只剩三件事：请求投影（Pydantic → 领域入参）、领域异常 → 契约码、响应投影。

关键口径：
1. ``file_url`` 必须在校验期**同步拉完**再返回 202：超限要能回 413、类型不支持和地址不可读
   要能回 400，把文件留到后台任务再拉，调用方只会看到一个永远 queued 的任务；
2. 202 前必须先把任务行提交：FastAPI 的响应后置任务与依赖退出都在响应发出之后才执行，
   未提交的行后台读不到（POC 期踩过的坑）；
3. 解析失败属于**任务结果**：``#02`` 仍返回 HTTP 200 + ``error_code``，不能把异常抛给
   #01 的调用方（那时 202 早已返回）。
"""

from typing import Any

from app.modules.kb.parse_job.pipeline import (
    ParseFileTooLargeError,
    ParseInvalidRequestError,
    fetch_file as _fetch_file,
    run_parse_job as _run_parse_job,
    validate_file_request as _validate_file_request,
)
from app.modules.kb.parse_job.service import KbParseJobService, ParseJobSnapshot
from app.modules.kb.parse_result.preview import refresh_pages

from ...base.errors import DEFAULT_MESSAGES, INVALID_REQUEST, PARSE_FAILED, PAYLOAD_TOO_LARGE, ContractApiError
from .schema import ParseJobRequest

PARSE_JOB_SERVICE = KbParseJobService()


def _map_parse_error(exc: ParseInvalidRequestError | ParseFileTooLargeError) -> ContractApiError:
    """领域异常 → 契约码：超限 413、其余请求问题 400；领域层不感知 HTTP。"""
    if isinstance(exc, ParseFileTooLargeError):
        return ContractApiError(PAYLOAD_TOO_LARGE, str(exc))
    return ContractApiError(INVALID_REQUEST, str(exc))


def validate_file_request(payload: ParseJobRequest) -> None:
    """类型白名单与声明大小的前置校验（拉取前失败，不产生任务行）。"""
    try:
        _validate_file_request(payload.file_name, payload.file_size)
    except (ParseInvalidRequestError, ParseFileTooLargeError) as exc:
        raise _map_parse_error(exc) from exc


async def fetch_file(url: str) -> bytes:
    """同步拉取 ``file_url`` 并在超限时立刻中断：容量与可读性都必须在 202 之前判掉。"""
    try:
        return await _fetch_file(url)
    except (ParseInvalidRequestError, ParseFileTooLargeError) as exc:
        raise _map_parse_error(exc) from exc


async def create_parse_job_record(payload: ParseJobRequest) -> tuple[ParseJobSnapshot, bool]:
    """把请求快照转成任务行（``#01`` 202 前必须已提交）。"""
    return await PARSE_JOB_SERVICE.create_job(
        job_id=payload.job_id,
        resource_type=payload.resource_type,
        file_meta=_file_meta(payload),
    )


def _file_meta(payload: ParseJobRequest) -> dict[str, Any]:
    """落库的请求快照：``#02`` 不展示它，只用于排障与重放时还原入参。"""
    return {
        "file_name": payload.file_name,
        "file_url": payload.file_url,
        "doc_id": payload.doc_id,
        "version_id": payload.version_id,
        "tenant_id": payload.tenant_id,
        "file_id": payload.file_id,
        "file_size": payload.file_size,
        "metadata": payload.metadata,
        "options": payload.options.model_dump(),
        "prompt_text": payload.prompt_text,
        "model_key": payload.model_key,
    }


async def run_parse_job(payload: ParseJobRequest, content: bytes) -> None:
    """响应后置任务：转交领域流水线，并补上「任务失败」的契约词汇。

    失败码与兜底话术落库后由 ``#02`` 原样回传，属于契约侧的词汇，所以由本层注入。
    """
    await _run_parse_job(
        job_id=payload.job_id,
        file_name=payload.file_name,
        data=content,
        chunk_enabled=payload.options.chunk,
        resource_type=payload.resource_type,
        prompt_text=payload.prompt_text,
        model_key=payload.model_key,
        metadata=payload.metadata,
        language=payload.options.language,
        failure_code=PARSE_FAILED,
        failure_message=DEFAULT_MESSAGES[PARSE_FAILED],
    )


async def get_parse_job_snapshot(job_id: str) -> ParseJobSnapshot | None:
    """``#02`` 读取任务快照（成功态带产物）。"""
    return await PARSE_JOB_SERVICE.get_snapshot(job_id)


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
            # 页图是对象存储的预签名 URL，按当前配置重签：库里存的那份只作快照
            "pages": refresh_pages(snapshot.pages or []),
            "chunks": snapshot.chunks or [],
            "warnings": snapshot.warnings or [],
        }
        file_name = str(snapshot.file_meta.get("file_name") or "").strip()
        if file_name:
            result["file_name"] = file_name
        if snapshot.suggested_doc_type:
            result["suggested_doc_type"] = snapshot.suggested_doc_type
        if snapshot.suggested:
            result["suggested"] = snapshot.suggested
        body["result"] = result
    if snapshot.status == "failed":
        body["error_code"] = snapshot.error_code or PARSE_FAILED
        body["error_message"] = snapshot.error_message or DEFAULT_MESSAGES[PARSE_FAILED]
    return body
