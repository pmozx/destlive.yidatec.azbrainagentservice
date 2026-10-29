"""#01/#02 的最小解析链路：拉文件 → 解析 → 分片 → 产物结构，以及在后台跑完一次任务。

复用基座既有能力，不为 POC 另写一套（计划 T0.2 说明「复用基座工程」）：
- 文件拉取：``httpx`` 流式下载 + 大小上限（契约 10MB → 413 `payload_too_large`）；
- 内容提取：``integration_kb.pipeline.parsers.parse_document``（文本/HTML 内置，PDF/Office 走 MinerU）；
- 分片：``integration_kb.pipeline.chunker.split_text``（段落优先、句末切分、相邻重叠）；
- 阻塞调用一律进解析线程池（``run_in_parse_pool``），不占事件循环。

POC 明确不做（正式实现见对应任务）：
- 页图渲染（`pages` 恒为 `[]`，T2.4）；
- LLM 生成 `suggested`（POC 用 metadata 与正文启发式，T2.3）；
- 任务取消、四态状态机之外的重试与并发控制（T2.2）。
"""

import time
from pathlib import Path
from typing import Any, Final

import httpx

from app.config.setting import settings
from app.core.clients.base import trust_env_for_url
from app.core.database import async_session_factory
from app.core.logger import logger
from app.modules.integration_kb.pipeline.chunker import split_text
from app.modules.integration_kb.pipeline.parsers import parse_document
from app.modules.integration_kb.runtime.executor import run_in_parse_pool

from . import store
from .errors import PARSE_FAILED, PAYLOAD_TOO_LARGE, PocApiError
from .metrics import StageTimer

# 契约固定 10MB 上限（POC_V1_MAX_FILE_MB 可调，但不能无上限）
_MAX_BYTES: Final[int] = settings.POC_V1_MAX_FILE_MB * 1024 * 1024
# 摘要兜底长度：metadata 未给 summary 时截正文前 120 字
_SUMMARY_CHARS: Final[int] = 120


async def fetch_file(file_url: str) -> bytes:
    """流式拉取文件，超限立刻中断并抛 413（不把整包读完再判断）。"""
    total = 0
    chunks: list[bytes] = []
    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True, trust_env=trust_env_for_url(file_url)) as client:
            async with client.stream("GET", file_url) as response:
                if response.status_code >= 400:
                    raise PocApiError(
                        "invalid_request",
                        f"file_url 拉取失败（HTTP {response.status_code}），请确认链接可访问且有效期覆盖解析过程。",
                        status_code=400,
                    )
                async for block in response.aiter_bytes():
                    total += len(block)
                    if total > _MAX_BYTES:
                        raise PocApiError(
                            PAYLOAD_TOO_LARGE,
                            f"文件超过 {settings.POC_V1_MAX_FILE_MB}MB 上限，请拆分后重新提交。",
                            status_code=413,
                        )
                    chunks.append(block)
    except httpx.HTTPError as exc:
        raise PocApiError("invalid_request", f"file_url 拉取失败：{type(exc).__name__}", status_code=400) from exc

    if total == 0:
        raise PocApiError("invalid_request", "file_url 返回空内容，无法解析。", status_code=400)
    return b"".join(chunks)


def _compact(payload: dict[str, Any]) -> dict[str, Any]:
    """去掉值为 None 的键：契约里大量字段「可省略或 null」，省略比显式 null 更贴近示例。"""
    return {key: value for key, value in payload.items() if value is not None}


def _detect_page(page_map: list[tuple[str, int]], content: str) -> int | None:
    """从解析器的「段落 → 页码」映射里找分片所属页（找不到就返回 None）。"""
    if not page_map:
        return None
    head = content[:20]
    for text, page in page_map:
        if text and text[:20] in head:
            return page
    return None


def build_chunks(parsed_text: str, *, chunk_enabled: bool, page_map: list[tuple[str, int]]) -> list[dict[str, Any]]:
    """分片并转成契约 `result.chunks` 结构（seq 从 1 开始，声明本次列表内唯一）。"""
    if not chunk_enabled:
        return []
    entries: list[dict[str, Any]] = []
    for piece in split_text(parsed_text):
        content = piece.content.strip()
        if not content:
            continue
        page = _detect_page(page_map, content)
        entries.append(
            _compact(
                {
                    "seq": len(entries) + 1,
                    "content": content,
                    "page_from": page,
                    "page_to": page,
                    "source_type": "text",
                }
            )
        )
    return entries


def build_suggested(metadata: dict[str, Any], file_name: str, text: str) -> dict[str, Any]:
    """AI 建议元数据：POC 取用户已填值，缺省时用文件名与正文首段兜底（不覆盖人工选择）。"""
    suggested: dict[str, Any] = {}
    title = (metadata.get("title") or "").strip() or Path(file_name).stem
    if title:
        suggested["title"] = title
    summary = (metadata.get("summary") or "").strip() or text.strip()[:_SUMMARY_CHARS]
    if summary:
        suggested["summary"] = summary
    tags = metadata.get("tags") or []
    suggested["tags"] = [str(tag) for tag in tags]
    return suggested


def build_result(
    *,
    request_body: dict[str, Any],
    text: str,
    chunks: list[dict[str, Any]],
    page_map: list[tuple[str, int]],
    warnings: list[str],
) -> dict[str, Any]:
    """组装契约 `result`：`pages` 在 POC 恒为空数组（不伪造图像 URL）。"""
    file_name = str(request_body.get("file_name") or "")
    metadata = request_body.get("metadata") or {}
    result: dict[str, Any] = {
        "file_name": file_name,
        "suggested_doc_type": metadata.get("doc_type") or "document",
        "suggested": build_suggested(metadata, file_name, text),
        "pages": [],
        "chunks": chunks,
        "warnings": warnings,
    }
    if page_map:
        result["warnings"] = [*warnings, "POC 未渲染页图，pages 返回空数组（页图能力见 T2.4）。"]
    return result


async def _update_job(job_id: str, **values: Any) -> None:
    """独立事务更新任务状态：后台执行期间也要让 #02 轮询看到进度。"""
    try:
        async with async_session_factory() as session, session.begin():
            await store.update_job(session, job_id, **values)
    except Exception:
        logger.warning("POC 任务状态更新失败：job_id={} values={}", job_id, values, exc_info=True)


async def run_parse_job(job_id: str) -> None:
    """后台执行一次解析任务：失败写 `error_code/error_message`，HTTP 侧仍是 200。

    解析失败属于**任务结果**，不是请求失败（计划 §七.5），所以这里只更新任务状态，
    不向外抛异常。
    """
    async with async_session_factory() as session:
        job = await store.get_job(session, job_id)
    if job is None:
        logger.warning("POC 解析任务不存在：job_id={}", job_id)
        return
    if job["status"] in {"succeeded", "failed"}:
        return

    request_body = job["request_body"]
    timer = StageTimer()
    await _update_job(job_id, status="running", progress=10)

    try:
        async with timer.stage("fetch"):
            payload = await fetch_file(str(request_body.get("file_url") or ""))

        await _update_job(job_id, progress=40)

        file_name = str(request_body.get("file_name") or "unknown.txt")
        start = time.perf_counter()
        parsed = await run_in_parse_pool(parse_document, Path(file_name), data=payload)
        timer.record("parse", time.perf_counter() - start)

        await _update_job(job_id, progress=70)

        chunk_enabled = bool((request_body.get("options") or {}).get("chunk", True))
        warnings: list[str] = []
        if not (parsed.text or "").strip():
            raise PocApiError(PARSE_FAILED, "解析结果为空，无法生成切片。", status_code=400)
        if not chunk_enabled:
            warnings.append("options.chunk=false，本次不生成切片。")

        start = time.perf_counter()
        chunks = build_chunks(parsed.text, chunk_enabled=chunk_enabled, page_map=parsed.page_map)
        timer.record("chunk", time.perf_counter() - start)
        if chunk_enabled and not chunks:
            raise PocApiError(PARSE_FAILED, "切片结果为空，请检查文档正文是否为空。", status_code=400)

        result = build_result(
            request_body=request_body,
            text=parsed.text,
            chunks=chunks,
            page_map=parsed.page_map,
            warnings=warnings,
        )
        await _update_job(job_id, status="succeeded", progress=100, result=result)
        logger.info("POC 解析完成：job_id={} chunks={} 耗时={}ms", job_id, len(chunks), timer.total_ms)
        await _record_metric(job_id, timer)
    except Exception as exc:
        code = exc.code if isinstance(exc, PocApiError) else PARSE_FAILED
        message = exc.message if isinstance(exc, PocApiError) else f"{type(exc).__name__}: {exc}"
        logger.error("POC 解析失败：job_id={} code={} msg={}", job_id, code, message)
        await _update_job(job_id, status="failed", progress=100, error_code=code, error_message=message)
        await _record_metric(job_id, timer)


async def _record_metric(job_id: str, timer: StageTimer) -> None:
    async with async_session_factory() as session, session.begin():
        await store.record_metric(session, kind="parse", key=job_id, total_ms=timer.total_ms, stages=timer.snapshot())
