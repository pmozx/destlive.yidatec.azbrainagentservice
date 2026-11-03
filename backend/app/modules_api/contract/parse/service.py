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
   不能把异常抛给 #01 的调用方（那时 202 早已返回）；
4. 文档与 FAQ 共用 #02 结果结构：文档走「解析 → 分片」，FAQ 用 ``prompt_text`` 调模型提取
   「问题标题 + 答案正文」；建议元数据与告警都只做非致命降级，不影响任务成功。
"""

import asyncio
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from app.config.setting import settings
from app.core.clients.download import DOWNLOAD_TIMEOUT_SECONDS, DownloadError, download_bytes
from app.core.clients.llm import LlmClient
from app.core.exceptions import CustomException
from app.core.logger import logger
from app.core.rag import ChunkedDocument, parse_and_chunk, supported_suffixes
from app.core.rag.page_render import render_pdf_pages
from app.modules.kb.parse_job.service import ParseJobSnapshot, KbParseJobService
from app.modules.kb.parse_result.preview import pages_with_public_base, preview_available, save_pages
from app.utils.page_locator import PageLocator

from ..errors import DEFAULT_MESSAGES, INVALID_REQUEST, PARSE_FAILED, PAYLOAD_TOO_LARGE, ContractApiError
from .schema import ParseJobRequest

# 契约上限：单文件 10MB（正文里声明的 file_size 与实际拉取的字节都要校验）
MAX_FILE_BYTES: int = 10 * 1024 * 1024

# 中间进度：解析完成 → 分片/FAQ 完成 → 页图完成 → 建议完成 → 终态 100
PROGRESS_PARSED = 35
PROGRESS_CHUNKED = 55
PROGRESS_PAGES = 80
PROGRESS_SUGGESTED = 95

PARSE_JOB_SERVICE = KbParseJobService()

# FAQ 提取的格式约束（prompt_text 由 GSI 提供，这里只补「必须输出 JSON」的硬约束）
_FAQ_FORMAT_INSTRUCTION = (
    "请从给定文档中提取常见问题（FAQ）。只依据原文作答，不得编造原文没有的事实。\n"
    '输出必须是严格的 JSON 数组，每项形如 {"question": "问题标题", "answer": "答案正文"}；'
    "不要输出 JSON 以外的任何解释文字。"
)

# 模型调用键：GSI 当前默认传 default，映射到服务端配置的 LLM_MODEL
_DEFAULT_MODEL_KEY = "default"

_JSON_FENCE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$")


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
    """把分片投影成契约结构：``seq`` 从 1 起，页码/小标题有信息才回填（无则省略）。"""
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


def _json_array(raw: str) -> list[Any]:
    """从模型输出中取出 JSON 数组：兼容 ```json 围栏与前后解释文字。"""
    text = _JSON_FENCE.sub("", raw.strip()).strip()
    candidates = [text]
    start, end = text.find("["), text.rfind("]")
    if start >= 0 and end > start:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(payload, dict):
            payload = payload.get("faqs") or payload.get("items") or payload.get("data")
        if isinstance(payload, list):
            return payload
    raise CustomException(msg="FAQ 提取结果不是合法的 JSON 数组。", status_code=502)


def _faq_chunks(items: list[Any], *, page_map: list[tuple[str, int]]) -> tuple[list[dict[str, Any]], int]:
    """把模型返回的问答对投影成契约切片，返回 ``(chunks, 跳过条数)``。"""
    locator = PageLocator(page_map)
    chunks: list[dict[str, Any]] = []
    skipped = 0
    for item in items:
        if not isinstance(item, dict):
            skipped += 1
            continue
        question = str(item.get("question") or item.get("title") or item.get("q") or "").strip()
        answer = str(item.get("answer") or item.get("content") or item.get("a") or "").strip()
        if not question or not answer:
            skipped += 1
            continue
        row: dict[str, Any] = {
            "seq": len(chunks) + 1,
            "heading": question,
            "content": answer,
            "source_type": "text",
        }
        page_no = locator.locate(answer)
        if page_no is not None:
            row["page_from"] = page_no
            row["page_to"] = page_no
        chunks.append(row)
    return chunks, skipped


async def build_faq_chunks(
    document_text: str,
    *,
    prompt_text: str,
    model_key: str | None,
    language: str,
    page_map: list[tuple[str, int]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """按 GSI 提示词调模型提取 FAQ；返回 ``(chunks, warnings)``。

    模型未配置或调用失败会抛出可读异常，由任务统一落成 ``failed``（这是 FAQ 的硬依赖，
    不做「悄悄退化成普通文档切片」的降级，否则调用方会拿到与提示词无关的结果）。
    """
    text = document_text.strip()
    warnings: list[str] = []
    if len(text) > settings.PARSE_FAQ_MAX_CHARS:
        text = text[: settings.PARSE_FAQ_MAX_CHARS]
        warnings.append(f"文档正文超过 {settings.PARSE_FAQ_MAX_CHARS} 字符，FAQ 提取仅使用前 {settings.PARSE_FAQ_MAX_CHARS} 字符。")

    client = LlmClient()
    messages = [
        {"role": "system", "content": f"{prompt_text.strip()}\n\n{_FAQ_FORMAT_INSTRUCTION}"},
        {"role": "user", "content": f"文档语言：{language}\n\n文档正文：\n{text}"},
    ]
    model = model_key.strip() if model_key else ""
    answer = await client.chat(
        messages,
        temperature=0.1,
        model=model if model and model != _DEFAULT_MODEL_KEY else None,
    )
    chunks, skipped = _faq_chunks(_json_array(answer), page_map=page_map)
    if skipped:
        warnings.append(f"FAQ 提取结果中有 {skipped} 条缺少问题或答案，已跳过。")
    if not chunks:
        raise CustomException(msg="FAQ 提取结果为空：未找到有效的问题与答案。", status_code=502)
    return chunks, warnings


def _text_value(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def build_suggested(
    metadata: dict[str, Any] | None,
    text: str,
    file_name: str,
) -> tuple[dict[str, Any] | None, str | None]:
    """生成建议元数据：优先复用页面已填值，缺失时从正文推导（不额外调用模型）。

    GSI 已经传入 title/summary/tags/doc_type，这些本身就是页面侧的建议口径；
    文档没有填写时，用首个标题行与正文首段兜底，保证 ``suggested`` 不是空壳。
    """
    meta = metadata or {}
    title = _text_value(meta.get("title"))
    if not title:
        first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
        title = re.sub(r"^#{1,6}\s*", "", first_line).strip()
    if not title:
        title = Path(file_name).stem

    summary = _text_value(meta.get("summary"))
    if not summary:
        body = " ".join(
            line.strip()
            for line in text.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
        summary = body[:200]

    raw_tags = meta.get("tags")
    tags = [str(item).strip() for item in raw_tags if str(item).strip()] if isinstance(raw_tags, list) else []

    suggested: dict[str, Any] = {}
    if title:
        suggested["title"] = title
    if summary:
        suggested["summary"] = summary
    if tags:
        suggested["tags"] = tags
    doc_type = _text_value(meta.get("doc_type")) or None
    return (suggested or None), doc_type


async def _build_pages(
    job_id: str,
    file_name: str,
    data: bytes,
    warnings: list[str],
    *,
    preview_base_url: str,
) -> list[dict[str, Any]]:
    """PDF 页图渲染 + 落盘 + 签名 URL；任何失败都降级为告警并返回 ``[]``。"""
    if Path(file_name).suffix.lower() != ".pdf" or not preview_available():
        return []
    try:
        rendered = await asyncio.to_thread(
            render_pdf_pages,
            data,
            max_pages=settings.PARSE_PREVIEW_MAX_PAGES,
            scale=settings.PARSE_PREVIEW_SCALE,
        )
    except Exception as exc:  # noqa: BLE001 渲染失败不影响解析结果
        logger.warning("PDF 页图渲染失败（job_id={}）：{}", job_id, exc)
        warnings.append("PDF 页图渲染失败，未返回预览图。")
        return []
    if rendered.pages and rendered.total_pages > len(rendered.pages):
        warnings.append(f"文档共 {rendered.total_pages} 页，仅返回前 {len(rendered.pages)} 页预览图。")
    try:
        return await asyncio.to_thread(
            save_pages,
            job_id,
            rendered.pages,
            base_url=preview_base_url,
        )
    except Exception as exc:  # noqa: BLE001 落盘失败不影响解析结果
        logger.warning("页图预览保存失败（job_id={}）：{}", job_id, exc)
        warnings.append("页图预览保存失败，未返回预览图。")
        return []


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
            "options": payload.options.model_dump(),
            "prompt_text": payload.prompt_text,
            "model_key": payload.model_key,
        },
    )


async def get_parse_job_snapshot(job_id: str) -> ParseJobSnapshot | None:
    """``#02`` 读取任务快照（成功态带产物）。"""
    return await PARSE_JOB_SERVICE.get_snapshot(job_id)


async def run_parse_job(
    *,
    job_id: str,
    file_name: str,
    data: bytes,
    chunk_enabled: bool,
    resource_type: str,
    prompt_text: str | None,
    model_key: str | None,
    metadata: dict[str, Any],
    language: str,
    preview_base_url: str,
) -> None:
    """响应后置任务：解析 → 分片/FAQ → 页图 → 建议 → 写产物；异常统一转 failed。"""
    try:
        await PARSE_JOB_SERVICE.mark_running(job_id)
        chunked = await parse_and_chunk(Path(file_name), data=data)
        await PARSE_JOB_SERVICE.update_progress(job_id, PROGRESS_PARSED)

        text = chunked.document.text.strip()
        if not text:
            raise CustomException(msg="文档解析后无有效内容。", status_code=400)

        warnings: list[str] = []
        if len(text) < 20:
            warnings.append("解析内容过短，请人工确认解析质量。")

        if resource_type == "faq":
            if not prompt_text:
                raise CustomException(msg="FAQ 解析缺少 prompt_text。", status_code=400)
            chunks, faq_warnings = await build_faq_chunks(
                text,
                prompt_text=prompt_text,
                model_key=model_key,
                language=language,
                page_map=chunked.document.page_map,
            )
            warnings.extend(faq_warnings)
        else:
            chunks = build_result_chunks(chunked, enabled=chunk_enabled)
            if chunk_enabled and not chunks:
                raise CustomException(msg="文档解析后未生成有效切片。", status_code=400)
        await PARSE_JOB_SERVICE.update_progress(job_id, PROGRESS_CHUNKED)

        pages = await _build_pages(job_id, file_name, data, warnings, preview_base_url=preview_base_url)
        await PARSE_JOB_SERVICE.update_progress(job_id, PROGRESS_PAGES)

        suggested, suggested_doc_type = build_suggested(metadata, text, file_name)
        await PARSE_JOB_SERVICE.update_progress(job_id, PROGRESS_SUGGESTED)

        await PARSE_JOB_SERVICE.complete(
            job_id,
            chunks=chunks,
            pages=pages,
            suggested=suggested,
            suggested_doc_type=suggested_doc_type,
            warnings=warnings,
            content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
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
            # 页图 URL 按当前对外基址投影：旧任务存的是旧地址，换 IP/端口后仍能打开
            "pages": pages_with_public_base(snapshot.pages or []),
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
