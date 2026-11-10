"""解析流水线（#01 / #02 的执行体）：类型与容量校验 → 拉取 → 解析 → 切片/FAQ → 页图 → 建议。

为什么整条流水线在领域层而不是契约接入层：

- 它不产出任何契约字段，只产出**落库的产物**（``parse_result`` 的 chunks / pages / suggested）；
- 只要接入层再长一个出口（管理端补解析、批处理重跑），流程必须与 #01 完全一致，
  否则「同一个文件经不同入口解析出不同切片」；
- 类型白名单、容量上限、地址不可读都是业务判定，不是 HTTP 语义。

错误码映射与请求/响应投影留在接入层：领域只抛 :class:`ParseInvalidRequestError`
（请求不合法）与 :class:`ParseFileTooLargeError`（容量超限），不感知 HTTP 状态码。
失败码与兜底话术由调用方传入——它们要落库并由 ``#02`` 原样回传，属于调用方的词汇
（同 :meth:`KbParseJobService.reset_unfinished` 的口径）。

关键口径：
1. ``file_url`` 必须在校验期**同步拉完**再返回 202：超限要能回 413、类型不支持和地址
   不可读要能回 400，把文件留到后台任务再拉，调用方只会看到一个永远 queued 的任务；
2. 解析失败属于**任务结果**：``#02`` 仍返回 HTTP 200 + 失败码，不能把异常抛给 #01 的
   调用方（那时 202 早已返回）；
3. 文档与 FAQ 共用结果结构：文档走「解析 → 分片」，FAQ 用 ``prompt_text`` 调模型提取
   「问题标题 + 答案正文」；建议元数据与告警都只做非致命降级，不影响任务成功；
4. 解析/分片复用 ``app.core.rag``（解析 → 分片 → 向量化唯一实现），不在本层另写解析器。
"""

import asyncio
import hashlib
import json
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import status
from langchain_core.messages import HumanMessage, SystemMessage

from app.config.setting import settings
from app.core.agents import PURPOSE_PARSE_FAQ, load_profile
from app.core.clients import get_llm_client
from app.core.clients.download import DOWNLOAD_TIMEOUT_SECONDS, DownloadError, download_bytes
from app.core.clients.s3 import get_s3_client
from app.core.exceptions import CustomException
from app.core.logger import logger
from app.core.rag import ChunkedDocument, parse_and_chunk, supported_suffixes
from app.core.rag.page_render import render_pdf_pages
from app.core.rag.parsers import ParsedDocument
from app.utils.page_locator import PageLocator

from ..parse_result.preview import preview_available, upload_pages
from .artifacts import archive_document, artifacts_available
from .service import KbParseJobService
from .step import (
    PARSE_STEPS,
    STEP_ARTIFACTS,
    STEP_CHUNK,
    STEP_EXTRACT,
    STEP_FAILED,
    STEP_FAQ,
    STEP_PAGES,
    STEP_PERSIST,
    STEP_PROGRESS,
    STEP_SKIPPED,
    STEP_SUCCEEDED,
    STEP_SUGGEST,
    KbParseStepService,
)

PARSE_JOB_SERVICE = KbParseJobService()
STEP_SERVICE = KbParseStepService()

# FAQ 提取的质量与格式约束（prompt_text 由 GSI 提供，这里补「输出形状 + 条目自足」的硬约束）。
# 两类噪声必须堵住：问题脱离原文就看不懂（「它有哪些特点？」）、答案是半句话（「该研究旨在」），
# 这类条目入库后即使被检索命中，也回答不了用户。
_FAQ_FORMAT_INSTRUCTION = """请从给定文档中提取常见问题（FAQ）。只依据原文作答，不得编造原文没有的事实。
输出必须是严格的 JSON 数组，每项形如 {"question": "问题标题", "answer": "答案正文"}；不要输出 JSON 以外的任何解释文字。
每条必须同时满足以下要求，不满足就整条不输出，不要交半成品：
1. question 是完整、脱离原文也能看懂的独立问句：主语或对象要写具体（文档标题、系统名、研究方法、数据集等），
   不得用「该研究」「本文」「上述」「它」这类没有指代的说法，也不要输出「目的是什么？」这种缺少主语的泛问；
2. answer 必须是完整的句子，能独立回答 question：不得停在「旨在」「来自」「包括」「如下」「以及」这类话没说完的地方，
   不得用省略号截断，不得只写「同上」「参见上文」；
3. answer 要给出具体的结论、名称、数值或步骤，不能只是把问题换个说法复述一遍；
4. 原文本身断句不完整或缺关键信息时，跳过该条，不要猜测，也不要用自己的解释去补全。"""

# 答案残缺的外观特征：话没说完就收尾（悬空短语 + 可选标点），或被省略号截断。
# 提示词已要求模型不许这样输出，这里兜住模型不听话的情况：宁可少一条，也不入库半句话。
_INCOMPLETE_ANSWER_TAIL = re.compile(
    r"(?:旨在|来自|包括|包含|如下|以及|用于|其中|例如)[、，,：:；;（(【\[「“]*$|(?:…+|\.{3,})$"
)

# 模型调用键：GSI 当前默认传 default，映射到用途槽位（槽位为空再回落 LLM_MODEL）
_DEFAULT_MODEL_KEY = "default"

_JSON_FENCE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$")


class ParseInvalidRequestError(Exception):
    """请求不合法：类型不在白名单、文件地址不可读、内容为空或缺 ``prompt_text``。"""


class ParseFileTooLargeError(Exception):
    """文件超过大小上限：既不截断也不降级解析（截断后的产物会被当成完整内容写库）。"""


def max_file_bytes() -> int:
    """当前生效的单文件上限：取配置 ``PARSE_MAX_FILE_BYTES``（默认 10MB），运行时读取。"""
    return settings.PARSE_MAX_FILE_BYTES


def validate_file_request(file_name: str, file_size: int | None) -> None:
    """类型白名单与声明大小的前置校验（拉取前失败，不产生任务行）。"""
    suffix = Path(file_name).suffix.lower()
    if suffix not in supported_suffixes():
        raise ParseInvalidRequestError(
            f"暂不支持解析 {suffix or '无后缀'} 文件；已接入：{'/'.join(supported_suffixes())}。"
        )
    limit = max_file_bytes()
    if file_size is not None and file_size > limit:
        raise ParseFileTooLargeError(f"文件超过 {limit // (1024 * 1024)}MB 上限。")


async def fetch_file(url: str, *, max_bytes: int | None = None) -> bytes:
    """流式拉取 ``file_url`` 并在超限时立刻中断（下载实现与单例在 ``core.clients.download``）。"""
    limit = max_bytes if max_bytes is not None else max_file_bytes()
    try:
        return await download_bytes(url, max_bytes=limit, timeout=DOWNLOAD_TIMEOUT_SECONDS)
    except DownloadError as exc:
        if exc.too_large:
            raise ParseFileTooLargeError(str(exc)) from exc
        raise ParseInvalidRequestError(str(exc)) from exc


def build_result_chunks(chunked: ChunkedDocument, *, enabled: bool) -> list[dict[str, Any]]:
    """把分片投影成产物结构：``seq`` 从 1 起，页码/小标题有信息才回填（无则省略）。"""
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
    raise CustomException(msg="FAQ 提取结果不是合法的 JSON 数组。", status_code=status.HTTP_502_BAD_GATEWAY)


def _faq_chunks(items: list[Any], *, page_map: list[tuple[str, int]]) -> tuple[list[dict[str, Any]], int]:
    """把模型返回的问答对投影成产物切片，返回 ``(chunks, 跳过条数)``。

    跳过三类条目：非对象、缺问题或答案、答案是半句话（见 :data:`_INCOMPLETE_ANSWER_TAIL`）。
    """
    locator = PageLocator(page_map)
    chunks: list[dict[str, Any]] = []
    skipped = 0
    for item in items:
        if not isinstance(item, dict):
            skipped += 1
            continue
        question = str(item.get("question") or item.get("title") or item.get("q") or "").strip()
        answer = str(item.get("answer") or item.get("content") or item.get("a") or "").strip()
        if not question or not answer or _INCOMPLETE_ANSWER_TAIL.search(answer):
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

    模型未配置、调用失败或超过 ``PARSE_FAQ_TIMEOUT`` 都会抛出可读异常，由任务统一落成
    ``failed``（这是 FAQ 的硬依赖，不做「悄悄退化成普通文档切片」的降级，
    否则调用方会拿到与提示词无关的结果）。
    """
    text = document_text.strip()
    warnings: list[str] = []
    if len(text) > settings.PARSE_FAQ_MAX_CHARS:
        text = text[: settings.PARSE_FAQ_MAX_CHARS]
        warnings.append(
            f"文档正文超过 {settings.PARSE_FAQ_MAX_CHARS} 字符，FAQ 提取仅使用前 {settings.PARSE_FAQ_MAX_CHARS} 字符。"
        )

    client = get_llm_client()
    messages = [
        SystemMessage(content=f"{prompt_text.strip()}\n\n{_FAQ_FORMAT_INSTRUCTION}"),
        HumanMessage(content=f"文档语言：{language}\n\n文档正文：\n{text}"),
    ]
    # GSI 可指定模型标识；传 default（或空串）时走用途槽位，槽位再回落配置表默认行/env
    override = model_key.strip() if model_key else ""
    profile = await load_profile(
        PURPOSE_PARSE_FAQ,
        key=override if override and override != _DEFAULT_MODEL_KEY else None,
    )
    try:
        # 必须有整体预算：模型走流式，httpx 的读超时会被每片数据重置，
        # 半开连接或服务端极慢输出下 ``chat()`` 可以无限期不返回（任务永远停在 35%）。
        answer = await asyncio.wait_for(
            client.chat(messages, profile=profile),
            timeout=profile.timeout,
        )
    except TimeoutError as exc:
        raise CustomException(
            msg=f"FAQ 提取超时（超过 {settings.PARSE_FAQ_TIMEOUT:.0f} 秒未返回），请缩小文档或分段后重新提交。",
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
        ) from exc
    chunks, skipped = _faq_chunks(_json_array(answer), page_map=page_map)
    if skipped:
        warnings.append(f"FAQ 提取结果中有 {skipped} 条缺少问题或答案、或答案未写完，已跳过。")
    if not chunks:
        raise CustomException(msg="FAQ 提取结果为空：未找到有效的问题与答案。", status_code=status.HTTP_502_BAD_GATEWAY)
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
            line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")
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


class _StepTracker:
    """一步的执行句柄：``record`` 补 detail/output，``skip`` / ``degrade`` 说明没跑成的原因。

    ``skip`` 是本步无需执行（非 PDF、对象存储未启用）；``degrade`` 是试过但失败，
    且这一步不致命——两者都不抛，区别只在台账状态（skipped / failed）。
    """

    def __init__(self, step: str) -> None:
        self.step = step
        self._detail: dict[str, Any] = {}
        self._output: dict[str, Any] | None = None
        self._skip_reason: str | None = None
        self._fail_reason: str | None = None

    def record(self, *, detail: dict[str, Any] | None = None, output: dict[str, Any] | None = None) -> None:
        if detail:
            self._detail.update(detail)
        if output is not None:
            self._output = output

    def skip(self, reason: str) -> None:
        self._skip_reason = reason

    def degrade(self, reason: str) -> None:
        self._fail_reason = reason

    def outcome(self) -> tuple[str, str | None]:
        """本步的终态与原因（失败优先于跳过）。"""
        if self._fail_reason:
            return STEP_FAILED, self._fail_reason
        if self._skip_reason:
            return STEP_SKIPPED, self._skip_reason
        return STEP_SUCCEEDED, None

    def payload(self) -> tuple[dict[str, Any], dict[str, Any] | None]:
        return self._detail, self._output


def _error_code(exc: BaseException, step: str) -> str:
    """步骤失败码：``<STEP>_<异常类型>``，页面按它就能看出是哪一步、哪类问题。"""
    kind = "Timeout" if isinstance(exc, TimeoutError) else type(exc).__name__
    return f"{step.upper()}_{kind}"[:64]


def _error_message(exc: BaseException) -> str:
    """失败原因：业务异常本身就是给用户看的话术，其它异常带上类型名帮助定位。"""
    if isinstance(exc, CustomException):
        return exc.msg
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


@asynccontextmanager
async def _track_step(job_id: str, attempt: int, step: str) -> AsyncIterator[_StepTracker]:
    """跑一步并落台账：成功推进进度，失败写原因后原样抛出。

    为什么用上下文管理器而不是逐步 try/except：失败原因必须「一定」落到台账上
    （页面要能说清哪一步为什么失败），靠调用方自觉写必定有漏网的那一条。
    """
    tracker = _StepTracker(step)
    step_id = await STEP_SERVICE.start(job_id=job_id, attempt=attempt, step=step)
    try:
        yield tracker
    except Exception as exc:
        detail, _ = tracker.payload()
        await STEP_SERVICE.finish(
            step_id,
            status=STEP_FAILED,
            detail=detail,
            error_code=_error_code(exc, step),
            error_message=_error_message(exc),
        )
        raise
    detail, output = tracker.payload()
    status, reason = tracker.outcome()
    await STEP_SERVICE.finish(step_id, status=status, detail=detail, output=output, error_message=reason)
    await PARSE_JOB_SERVICE.update_progress(job_id, STEP_PROGRESS[step], attempt=attempt)


def _steps_for(resource_type: str) -> tuple[str, ...]:
    """该资源类型实际要走的步骤：``faq`` 与 ``chunk`` 互斥，其余相同。"""
    excluded = STEP_CHUNK if resource_type == "faq" else STEP_FAQ
    return tuple(step for step in PARSE_STEPS if step != excluded)


async def _resume_point(
    job_id: str,
    attempt: int,
    steps: tuple[str, ...],
    from_step: str | None,
) -> tuple[int, dict[str, dict[str, Any]]]:
    """确定续跑起点与其前序步骤的产物快照，返回 ``(起点下标, 快照表)``。

    起点默认取上一轮第一处 failed/running 的步骤；再往前回退到「前序步骤在上一轮
    全部成功」的位置——缺一份快照就只能从那一步重算，这是断点续跑的边界。
    """
    step = from_step if from_step in steps else None
    if step is None and attempt > 1:
        step = await STEP_SERVICE.first_unfinished(job_id, attempt=attempt - 1)
    if step not in steps:
        return 0, {}
    index = steps.index(step)
    if steps[index] in (STEP_ARTIFACTS, STEP_CHUNK):
        # 这两步吃的是解析器给的对象（图片字节 / 分片列表），事后再取不回来：
        # 只能与 extract 同跑。FAQ 续跑不受影响（只靠正文与页码快照）。
        index = 0
    while index > 0:
        ready, outputs = await STEP_SERVICE.completed_before(
            job_id, attempt=attempt, steps=list(steps[:index])
        )
        if ready:
            return index, outputs
        index -= 1
    return 0, {}


async def _load_source(object_key: str | None, file_url: str | None) -> bytes:
    """续跑时重新取原件：先取对象存储里的原件，再退回契约给的 ``file_url``。

    两者都取不到就不续跑——不能退化成「拿空文档解析」把产物写坏，
    失败原因会落在 extract 步骤上，页面能直接看到该重传原件。
    """
    if object_key and settings.S3_ENABLE:
        try:
            # boto3 是同步客户端，放线程里跑，避免拖住事件循环
            return await asyncio.to_thread(get_s3_client().download_bytes, object_key)
        except Exception as exc:  # noqa: BLE001 对象存储不可用时退回 file_url
            logger.warning("续跑读取原件失败（object_key={}）：{}", object_key, exc)
    if file_url:
        return await fetch_file(file_url)
    raise CustomException(
        msg="续跑失败：原件不在对象存储，任务也没有 file_url 可用于重新下载。",
        status_code=status.HTTP_400_BAD_REQUEST,
    )


async def _build_pages(
    job_id: str,
    data: bytes,
) -> tuple[list[dict[str, Any]], list[str], str | None]:
    """PDF 页图渲染 → 上传对象存储；返回 ``(pages, warnings, 失败原因)``。

    失败原因非空表示没拿到页图（渲染或上传失败），调用方记为「非致命失败」：
    页图只是辅助预览，不该让整份文档解析失败。
    """
    warnings: list[str] = []
    try:
        rendered = await asyncio.to_thread(
            render_pdf_pages,
            data,
            max_pages=settings.PARSE_PREVIEW_MAX_PAGES,
            scale=settings.PARSE_PREVIEW_SCALE,
        )
    except Exception as exc:  # noqa: BLE001 渲染失败不影响解析结果
        logger.warning("PDF 页图渲染失败（job_id={}）：{}", job_id, exc)
        return [], ["PDF 页图渲染失败，未返回预览图。"], f"PDF 页图渲染失败：{exc}"
    if rendered.pages and rendered.total_pages > len(rendered.pages):
        warnings.append(f"文档共 {rendered.total_pages} 页，仅返回前 {len(rendered.pages)} 页预览图。")
    try:
        # boto3 上传是同步阻塞调用，放线程里跑，避免拖住事件循环
        pages = await asyncio.to_thread(upload_pages, job_id, rendered.pages)
    except Exception as exc:  # noqa: BLE001 上传失败不影响解析结果
        logger.warning("页图预览保存失败（job_id={}）：{}", job_id, exc)
        warnings.append("页图预览保存失败，未返回预览图。")
        return [], warnings, f"页图上传对象存储失败：{exc}"
    return pages, warnings, None


async def run_parse_job(
    *,
    job_id: str,
    file_name: str,
    data: bytes | None,
    chunk_enabled: bool,
    resource_type: str,
    prompt_text: str | None,
    model_key: str | None,
    metadata: dict[str, Any],
    language: str,
    failure_code: str,
    failure_message: str,
    attempt: int = 1,
    from_step: str | None = None,
    object_key: str | None = None,
    file_url: str | None = None,
) -> None:
    """后台流水线：解析 → 归档产物 → FAQ/分片 → 页图 → 建议 → 写产物。

    每一步都落一行台账（含耗时、失败原因、续跑快照），失败一律转 ``failed``；
    ``failure_code`` / ``failure_message`` 由调用方给：它们落库后会被原样回传，
    取值属于调用方的词汇，领域层不预设。

    ``attempt`` / ``from_step`` 是断点续跑：起点之前的步骤从台账的 ``output`` 快照恢复，
    不重新解析——重跑一次 MinerU 是分钟级成本，用户只想换提示词重提 FAQ 或补一次页图时
    不该付这个代价。``data`` 为空时按 ``object_key`` → ``file_url`` 重新取原件。
    """
    steps = _steps_for(resource_type)
    index, outputs = await _resume_point(job_id, attempt, steps, from_step)
    todo = set(steps[index:])
    await PARSE_JOB_SERVICE.mark_running(job_id, attempt=attempt)

    try:
        # 续跑恢复：起点之前的步骤产物从台账快照取（没有快照时 _resume_point 已把起点前移）
        chunk_step = STEP_FAQ if resource_type == "faq" else STEP_CHUNK
        restored_extract = outputs.get(STEP_EXTRACT) or {}
        restored_chunks = outputs.get(chunk_step) or {}
        text = str(restored_extract.get("text") or "")
        page_map: list[tuple[str, int]] = [
            (str(item[0]), int(item[1])) for item in restored_extract.get("page_map") or []
        ]
        warnings: list[str] = list(restored_extract.get("warnings") or [])
        warnings.extend(restored_chunks.get("warnings") or [])
        chunks: list[dict[str, Any]] = list(restored_chunks.get("chunks") or [])
        pages: list[dict[str, Any]] = list((outputs.get(STEP_PAGES) or {}).get("pages") or [])
        artifacts: dict[str, Any] | None = dict((outputs.get(STEP_ARTIFACTS) or {}).get("artifacts") or {}) or None
        suggested = (outputs.get(STEP_SUGGEST) or {}).get("suggested")
        suggested_doc_type = (outputs.get(STEP_SUGGEST) or {}).get("doc_type")
        # 解析器对象只在 extract 同轮可得；不走 extract 时不依赖它们（见 _resume_point）
        document: ParsedDocument | None = None
        chunked: ChunkedDocument | None = None

        # ── extract：取源 + 解析（快照口径：正文/页码/告警，供后续步骤续跑） ──
        if STEP_EXTRACT in todo:
            async with _track_step(job_id, attempt, STEP_EXTRACT) as step:
                content = data if data is not None else await _load_source(object_key, file_url)
                chunked = await parse_and_chunk(Path(file_name), data=content)
                document = chunked.document
                text = document.text.strip()
                if not text:
                    raise CustomException(msg="文档解析后无有效内容。", status_code=status.HTTP_400_BAD_REQUEST)
                page_map = list(document.page_map)
                warnings = ["解析内容过短，请人工确认解析质量。"] if len(text) < 20 else []
                step.record(
                    detail={
                        "parser": str(document.meta.get("parser") or ""),
                        "pages": int(document.meta.get("pages") or 0),
                        "chars": len(text),
                        "images": len(document.images),
                        "section_chunks": len(chunked.chunks),
                    },
                    output={
                        "text": text,
                        "page_map": [[key, page] for key, page in page_map],
                        "warnings": warnings,
                        "meta": dict(document.meta),
                    },
                )

        # ── artifacts：MinerU 产物（md / 版面 JSON / 图片）归档到对象存储 ──────
        if STEP_ARTIFACTS in todo:
            async with _track_step(job_id, attempt, STEP_ARTIFACTS) as step:
                if not artifacts_available():
                    step.skip("对象存储未启用（S3_ENABLE=false），解析产物未归档。")
                elif document is None:
                    step.skip("本次执行未重新解析，图片字节不可得，跳过归档。")
                else:
                    # boto3 上传是同步阻塞调用，放线程里跑，避免拖住事件循环
                    captured = await asyncio.to_thread(archive_document, job_id, document)
                    artifacts = captured
                    step.record(
                        detail={"markdown": True, "images": captured["image_count"]},
                        output={"artifacts": captured},
                    )

        # ── faq / chunk：问答提取或文档切片（互斥，按 resource_type） ────────
        if chunk_step in todo:
            async with _track_step(job_id, attempt, chunk_step) as step:
                if resource_type == "faq":
                    if not prompt_text:
                        raise CustomException(
                            msg="FAQ 解析缺少 prompt_text。", status_code=status.HTTP_400_BAD_REQUEST
                        )
                    chunks, faq_warnings = await build_faq_chunks(
                        text,
                        prompt_text=prompt_text,
                        model_key=model_key,
                        language=language,
                        page_map=page_map,
                    )
                    warnings.extend(faq_warnings)
                else:
                    if chunked is None:
                        raise CustomException(
                            msg="续跑缺少解析快照，请从「解析」步骤重跑。",
                            status_code=status.HTTP_400_BAD_REQUEST,
                        )
                    chunks = build_result_chunks(chunked, enabled=chunk_enabled)
                    if chunk_enabled and not chunks:
                        raise CustomException(
                            msg="文档解析后未生成有效切片。", status_code=status.HTTP_400_BAD_REQUEST
                        )
                step.record(detail={"chunks": len(chunks)}, output={"chunks": chunks, "warnings": warnings})

        # ── pages：PDF 页图（非致命，失败只告警） ────────────────────────────
        if STEP_PAGES in todo:
            async with _track_step(job_id, attempt, STEP_PAGES) as step:
                if Path(file_name).suffix.lower() != ".pdf" or not preview_available():
                    step.skip("非 PDF 或未开启页图能力（PARSE_PREVIEW_ENABLE/S3_ENABLE）。")
                else:
                    content = data if data is not None else await _load_source(object_key, file_url)
                    pages, page_warnings, page_error = await _build_pages(job_id, content)
                    warnings.extend(page_warnings)
                    if page_error:
                        step.degrade(page_error)
                    else:
                        step.record(detail={"pages": len(pages)}, output={"pages": pages})

        # ── suggest：建议元数据（纯本地推导，不额外调模型） ──────────────────
        if STEP_SUGGEST in todo:
            async with _track_step(job_id, attempt, STEP_SUGGEST) as step:
                suggested, suggested_doc_type = build_suggested(metadata, text, file_name)
                step.record(
                    detail={"title": str((suggested or {}).get("title") or "")},
                    output={"suggested": suggested, "doc_type": suggested_doc_type},
                )

        # ── persist：写产物 + 终态（同一事务；attempt 不匹配则整份丢弃） ──────
        async with _track_step(job_id, attempt, STEP_PERSIST) as step:
            written = await PARSE_JOB_SERVICE.complete(
                job_id,
                chunks=chunks,
                pages=pages,
                suggested=suggested,
                suggested_doc_type=suggested_doc_type,
                warnings=warnings,
                artifacts=artifacts,
                content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                attempt=attempt,
            )
            if not written:
                raise CustomException(
                    msg="本次执行已被新的执行接管，产物未落库。", status_code=status.HTTP_409_CONFLICT
                )
            step.record(detail={"chunks": len(chunks), "pages": len(pages)})
    except Exception as exc:
        message = exc.msg if isinstance(exc, CustomException) else failure_message
        logger.exception("解析任务失败（job_id={}）：{}", job_id, exc)
        await PARSE_JOB_SERVICE.fail(
            job_id, error_code=failure_code, error_message=message, attempt=attempt
        )
