"""`/v1` 路由（POC 骨架）：#01 受理 → #02 轮询 → #03 写入 → #07 检索 → #14 生成。

为什么整组路由单独挂 `/v1`：
1. 调用方是 GSI 后端，鉴权走四头 HMAC 签名（`dependencies=[Depends(verified_signature)]`），
   与 `/api/v1` 的 JWT Bearer 完全是两套体系，不能混在一个前缀下；
2. 错误体是裸 `{"error": {...}}`，与 `/api/v1` 的 `code/msg/data` 信封不同 ——
   靠 `PocApiRoute` 在路由层转换，不动基座的全局异常处理器；
3. 路由函数只做「校验顺序 + 事务边界 + 回执拼装」，解析/写入/检索/生成分别在
   `parser.py` / `publish.py` / `retrieval.py` / `chat.py`，便于 T1.x 按生产标准逐个替换。
"""

from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import logger

from . import chat, parser, publish, retrieval, store
from .deps import poc_db, verified_signature
from .errors import IDEMPOTENCY_CONFLICT, NOT_FOUND, PocApiError, PocApiRoute, PocErrorResponse
from .metrics import StageTimer
from .schemas import (
    ChatCompletionRequest,
    KnowledgeEntryRequest,
    KnowledgeSearchRequest,
    ParseJobRequest,
    request_digest,
)
from .signature import verify_authorization_window

DbSession = Annotated[AsyncSession, Depends(poc_db)]

# 每个 /v1 操作都会出现的响应。写进 responses 才能在 Swagger 上看到错误体：
# 本组路由的错误体是裸 `{"error": {...}}`，不展示就只剩一个 4xx 状态码，联调时无从对字段。
COMMON_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": PocErrorResponse, "description": "参数校验失败或业务前置校验失败（`invalid_request`）"},
    401: {
        "model": PocErrorResponse,
        "description": "四头签名缺失 / 不符 / 时间戳越窗 / nonce 重放（`unauthorized`），响应带 `WWW-Authenticate`",
    },
    # FastAPI 有请求体/路径参数时默认会记一笔 422。`PocApiRoute` 已把参数校验错误归一成
    # 400 invalid_request，这里占位改写描述，避免联调方按 422 写错误分支。
    422: {
        "model": PocErrorResponse,
        "description": "POC **不会返回 422**：参数校验失败已归一为 400 `invalid_request`（本条仅用于抑制默认说明）",
    },
}

poc_router = APIRouter(
    prefix="/v1",
    tags=["POC /v1（T0.1–T0.4 验证骨架）"],
    route_class=PocApiRoute,
    dependencies=[Depends(verified_signature)],
    responses=COMMON_RESPONSES,
)


@poc_router.post(
    "/parse/jobs",
    status_code=status.HTTP_202_ACCEPTED,
    summary="#01 提交文档解析任务",
    responses={413: {"model": PocErrorResponse, "description": "文件超过大小上限（`payload_too_large`）"}},
)
async def create_parse_job(
    payload: ParseJobRequest,
    background: BackgroundTasks,
    db: DbSession,
) -> JSONResponse:
    """受理解析任务并立即返回 202：真正解析放在响应之后的背景任务里做。"""
    digest = request_digest(payload)
    existing = await store.ensure_idempotent(db, scope="parse", key=payload.job_id, body_hash=digest)
    if existing is not None:
        logger.info("POC #01 幂等重试，返回首次受理回执：job_id={}", payload.job_id)
        return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=existing)

    await store.insert_job(
        db,
        job_id=payload.job_id,
        body_hash=digest,
        request_body=payload.model_dump(mode="json"),
    )
    receipt: dict[str, Any] = {"job_id": payload.job_id, "status": "queued", "message": "任务已受理，请轮询 #02 查询进度。"}
    await store.put_idempotency(db, scope="parse", key=payload.job_id, body_hash=digest, response=receipt)
    # 任务行必须先提交：客户端一收到 202 就可能轮询 #02，后台解析器也要能读到这一行。
    # （依赖退出在响应之后才提交，不能指望它——这里显式提交）
    await db.commit()
    background.add_task(parser.run_parse_job, payload.job_id)
    logger.info("POC #01 受理：job_id={} file_name={}", payload.job_id, payload.file_name)
    return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=receipt, background=background)


@poc_router.get(
    "/parse/jobs/{job_id}",
    summary="#02 查询解析任务状态与产物",
    responses={404: {"model": PocErrorResponse, "description": "任务不存在或已过期（`not_found`）"}},
)
async def get_parse_job(job_id: str, db: DbSession) -> dict[str, Any]:
    """轮询接口：`failed` 也是 200，错误信息在 `error_code/error_message` 里（契约 §七.5）。"""
    job = await store.get_job(db, job_id)
    if job is None:
        raise PocApiError(NOT_FOUND, "解析任务不存在或已过期，请重新提交 #01。", status_code=status.HTTP_404_NOT_FOUND)

    payload: dict[str, Any] = {"job_id": job["job_id"], "status": job["status"], "progress": job["progress"]}
    if job["result"] is not None:
        payload["result"] = job["result"]
    if job["error_code"]:
        payload["error_code"] = job["error_code"]
        payload["error_message"] = job["error_message"] or ""
    return payload


@poc_router.post(
    "/knowledge/entries",
    status_code=status.HTTP_201_CREATED,
    summary="#03 首次发布写入正式切片",
    responses={
        409: {"model": PocErrorResponse, "description": "`publication_id` 已存在，首次发布不可隐式覆盖（POC 用 `idempotency_conflict`）"},
    },
)
async def create_knowledge_entry(payload: KnowledgeEntryRequest, db: DbSession) -> JSONResponse:
    """首发布写入：`publication_id` 已存在一律 409，不做隐式覆盖。"""
    digest = request_digest(payload)
    existing = await store.ensure_idempotent(db, scope="entries", key=payload.request_id, body_hash=digest)
    if existing is not None:
        logger.info("POC #03 幂等重试，返回首次回执：request_id={}", payload.request_id)
        return JSONResponse(status_code=status.HTTP_201_CREATED, content=existing)

    if await store.get_publication(db, payload.publication_id) is not None:
        raise PocApiError(
            IDEMPOTENCY_CONFLICT,
            f"publication_id={payload.publication_id} 已存在，首次发布不可隐式覆盖。",
            status_code=status.HTTP_409_CONFLICT,
        )

    timer = StageTimer()
    receipt = await publish.write_publication(db, payload, body_hash=digest, timer=timer)
    await store.put_idempotency(db, scope="entries", key=payload.request_id, body_hash=digest, response=receipt)
    await store.record_metric(db, kind="publish", key=payload.request_id, total_ms=timer.total_ms, stages=timer.snapshot())
    return JSONResponse(status_code=status.HTTP_201_CREATED, content=receipt)


@poc_router.post("/knowledge/search", summary="#07 关键词 / 自然语言检索")
async def search_knowledge(payload: KnowledgeSearchRequest, db: DbSession) -> dict[str, Any]:
    """两模式检索：`keyword` 单走关键词，`natural_language` 走向量 + 关键词 → RRF 融合。"""
    verify_authorization_window(
        scope_id=payload.authorization.scope_id,
        issued_at=payload.authorization.issued_at,
        expires_at=payload.authorization.expires_at,
    )
    timer = StageTimer()
    hits = await retrieval.retrieve(
        db,
        query=payload.query,
        mode=payload.mode,
        limit=payload.limit,
        timer=timer,
    )
    public_hits = [retrieval.public_hit(hit) for hit in hits]
    await store.record_metric(db, kind="search", key=payload.request_id, total_ms=timer.total_ms, stages=timer.snapshot())
    logger.info(
        "POC #07 完成：request_id={} mode={} hits={} 耗时={}ms",
        payload.request_id,
        payload.mode,
        len(public_hits),
        timer.total_ms,
    )
    return {
        "request_id": payload.request_id,
        "hits": public_hits,
        "returned_count": len(public_hits),
        "no_evidence": not public_hits,
    }


@poc_router.post(
    "/chat/completions",
    summary="#14 发送问题并生成回答",
    responses={500: {"model": PocErrorResponse, "description": "模型服务不可用 / 超时等内部错误（`internal_error`，`retryable=true`）"}},
)
async def create_chat_completion(payload: ChatCompletionRequest, db: DbSession) -> dict[str, Any]:
    """非流式生成：检索 → 拼上下文 → 生成 → citations / source_snapshots。"""
    verify_authorization_window(
        scope_id=payload.authorization.scope_id,
        issued_at=payload.authorization.issued_at,
        expires_at=payload.authorization.expires_at,
    )
    return await chat.answer(db, payload)
