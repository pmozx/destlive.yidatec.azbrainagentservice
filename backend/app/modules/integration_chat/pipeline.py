"""问答链路编排：预处理 → 检索 → 置信 → 生成 → 后处理，产出 SSE 事件流。

为什么不用 LangGraph 状态图：本链路是一条直线（没有需要模型自行决定的分支），
五个环节用顺序代码表达最直观，也便于按帧调试；分支只有「固定话术 / 拒答 / 正常」三处，
用 if 表达比图更清楚。

帧序契约：
- 正常：reasoning(analyzing) → reasoning(retrieving) → answer×N → sources → followups → done
- 拒答：reasoning(analyzing) → reasoning(retrieving) → error(notFound) → done
- 故障：error(code) → done（不落 assistant，半截答案不进历史）

口径：
1. ``done`` 帧永远在末尾，且带落库后的 ``messageId``；
2. 检索故障与「库里没有」严格区分：前者 retrievalUnavailable，后者 notFound；
3. 客户端断开即停止消费（不再往下写库），避免把半截答案落成历史。
"""

import asyncio
import time
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import suppress

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.setting import settings
from app.core.chat.streaming import StreamInterruptedError, StreamTimeoutError, close_stream, iter_stream
from app.core.clients import ChatMessage, ChatStreamChunk, LlmClient
from app.core.logger import logger
from app.modules.integration_kb.retrieval import space_option_list

from . import prompt
from .cite import build_cites, build_followups, sanitize_citations
from .conversation import ROLE_ASSISTANT, prepare_turn, save_assistant, to_history_messages, touch_cited_documents
from .preprocess import QueryPlan, build_query_plan
from .rerank import Candidate, grade, rerank_candidates, to_candidates
from .retriever import retrieve
from .schema import ChatStreamInSchema, CiteItemSchema
from .session.schema import MessageCreateSchema


class PipelineError(Exception):
    """链路错误（映射为 error 帧 + done；是否落库由 code 决定）。

    - ``notFound``：落 assistant（空召回/低置信属业务结果）；
    - ``retrievalUnavailable`` / ``generationTimeout`` / ``generationInterrupted`` /
      ``internalError``：不落 assistant（半截答案或故障不进历史）。
    """

    __slots__ = ("code", "message")

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


async def run_chat_pipeline(
    *,
    request: Request,
    db: AsyncSession,
    data: ChatStreamInSchema,
) -> AsyncGenerator[dict, None]:
    """执行一次问答，产出 ``{"event": str, "data": dict}`` 事件流。"""
    started_at = time.monotonic()
    question = (data.question or "").strip()
    if not question:
        yield _event("error", {"code": "internalError", "message": "问题不能为空"})
        yield _done_frame(0, 0, 0, started_at)
        return

    # ① 问答范围：只认「启用的空间」，指定空间必须落在其中
    allowed_space_ids = await _resolve_scope(db, data)
    if not allowed_space_ids:
        logger.info(f"问答无可用空间：scope_type={data.scope_type} space_ids={data.space_ids}")
        yield _event("error", {"code": "noSpace", "message": prompt.SPACE_SCOPE_MESSAGE})
        yield _done_frame(0, 0, 0, started_at)
        return

    # ② 会话 + 历史 + user 消息（独立事务先落库）
    try:
        session_id, history = await prepare_turn(
            session_id=data.session_id,
            question=question,
            scope_type=data.scope_type,
            space_ids=data.space_ids,
            turns=settings.KB_HISTORY_TURNS,
        )
    except ValueError as exc:
        yield _event("error", {"code": "internalError", "message": str(exc)})
        yield _done_frame(0, 0, 0, started_at)
        return

    plan = build_query_plan(question, allowed_space_ids, history)

    # ③ 固定话术（问模型/提示词/用量）：不检索、不调 LLM
    if plan.is_direct:
        yield _event("reasoning", {"stage": "analyzing", "text": prompt.ANALYZING_TEXT})
        yield _event("answer", {"delta": prompt.SYSTEM_META_MESSAGE})
        message_id = await _persist_assistant(
            session_id=session_id,
            answer=prompt.SYSTEM_META_MESSAGE,
            plan=plan,
            candidates=[],
            cites=[],
            follow_ups=[],
            not_found=False,
            retrieval=_empty_retrieval(),
            tokens_in=0,
            tokens_out=0,
            started_at=started_at,
        )
        yield _done_frame(message_id, 0, 0, started_at)
        return

    # ④ 拒答分支（闲聊/库外）：补一个 retrieving 帧保持帧序，不调外部服务
    if plan.is_reject:
        yield _event("reasoning", {"stage": "analyzing", "text": prompt.ANALYZING_TEXT})
        yield _event("reasoning", {"stage": "retrieving", "text": prompt.CHITCHAT_MESSAGE})
        yield _event("error", {"code": "notFound", "message": prompt.NOT_FOUND_MESSAGE})
        message_id = await _persist_assistant(
            session_id=session_id,
            answer=prompt.NOT_FOUND_MESSAGE,
            plan=plan,
            candidates=[],
            cites=[],
            follow_ups=[],
            not_found=True,
            retrieval=_empty_retrieval(),
            tokens_in=0,
            tokens_out=0,
            started_at=started_at,
        )
        yield _done_frame(message_id, 0, 0, started_at)
        return

    # ⑤ 预处理帧（立即下发，不等检索，首字不被检索耗时拖住）
    analyzing: dict = {"stage": "analyzing", "text": prompt.ANALYZING_TEXT}
    if plan.rewritten and plan.rewritten != plan.normalized:
        analyzing["optimized"] = plan.rewritten
    yield _event("reasoning", analyzing)
    logger.info(
        f"问答预处理：session={session_id} intent={plan.intent} injection={plan.injection} keywords={plan.keywords[:5]}"
    )

    yield _event(
        "reasoning",
        {"stage": "retrieving", "text": prompt.RETRIEVING_TEXT.format(space_count=len(allowed_space_ids), mode="向量检索")},
    )

    # ⑥ 检索（故障与「库里没有」严格区分）
    try:
        async with asyncio.timeout(settings.EMBEDDING_TIMEOUT + 30):
            hits = await retrieve(db, plan.rewritten, space_ids=allowed_space_ids)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 检索异常一律「服务不可用」，不能误判为库里没有
        logger.error(f"知识检索失败（retrievalUnavailable）：session={session_id} {exc}")
        yield _event("error", {"code": "retrievalUnavailable", "message": prompt.RETRIEVAL_UNAVAILABLE_MESSAGE})
        yield _done_frame(0, 0, 0, started_at)
        return

    # ⑦ 置信：精排 → TopN → 三闸
    candidates = to_candidates(hits)
    reranked, used = await rerank_candidates(plan.rewritten, candidates, settings.KB_RERANK_TOP_N)
    result = grade(reranked, reranked=used)
    retrieval = {
        "recalled": len(candidates),
        "reranked": len(reranked),
        "selected": len(result.candidates),
        "threshold": result.threshold,
        "rerankUsed": used,
    }
    if not result.candidates:
        logger.info(
            f"问答置信不足（拒答）：session={session_id} recalled={len(candidates)} "
            f"reranked={used} threshold={result.threshold} reason={result.reason}"
        )
        yield _event("error", {"code": "notFound", "message": prompt.NOT_FOUND_MESSAGE})
        message_id = await _persist_assistant(
            session_id=session_id,
            answer=prompt.NOT_FOUND_MESSAGE,
            plan=plan,
            candidates=[],
            cites=[],
            follow_ups=[],
            not_found=True,
            retrieval=retrieval,
            tokens_in=0,
            tokens_out=0,
            started_at=started_at,
        )
        yield _done_frame(message_id, 0, 0, started_at)
        return

    # ⑧ 生成：引用快照先建好，正文里的 [n] 才有意义
    cites = build_cites(result.candidates, keywords=plan.keywords)
    messages = _build_messages(plan, result.candidates, history)
    answer_parts: list[str] = []
    tokens_in = tokens_out = 0
    stream = LlmClient(timeout=settings.KB_LLM_TIMEOUT).stream_chat_events(
        messages,
        temperature=0.0 if plan.injection else settings.KB_ANSWER_TEMPERATURE,
        max_tokens=settings.KB_ANSWER_MAX_TOKENS,
    )
    try:
        async for chunk in _iterate_stream(stream, request):
            if chunk.delta:
                answer_parts.append(chunk.delta)
                yield _event("answer", {"delta": chunk.delta})
            # 用量只在末帧给全：有值就覆盖，无值沿用
            tokens_in = chunk.tokens_in or tokens_in
            tokens_out = chunk.tokens_out or tokens_out
    except PipelineError as exc:
        logger.warning(f"问答链路中断（{exc.code}）：session={session_id} {exc.message}")
        yield _event("error", {"code": exc.code, "message": exc.message})
        yield _done_frame(0, 0, 0, started_at)
        return
    finally:
        await _close_stream(stream)

    answer = "".join(answer_parts).strip()
    if not answer:
        logger.warning(f"模型返回空正文（generationInterrupted）：session={session_id}")
        yield _event("error", {"code": "generationInterrupted", "message": prompt.GENERATION_INTERRUPTED_MESSAGE})
        yield _done_frame(0, 0, 0, started_at)
        return

    # ⑨ 后处理：引用编号校验 + 问题拓展
    cleaned, invalid = sanitize_citations(answer, len(cites))
    if invalid:
        logger.warning(f"正文引用编号越界已摘除：session={session_id} invalid={invalid} cite_count={len(cites)}")
    answer = cleaned.strip() or answer
    follow_ups = build_followups(result.candidates, question)

    # 事件帧在 _generate 里逐字下发，这里补 sources / followups
    yield _event("sources", {"cites": [cite.model_dump(by_alias=True) for cite in cites]})
    if follow_ups:
        yield _event("followups", {"questions": follow_ups})

    message_id = await _persist_assistant(
        session_id=session_id,
        answer=answer,
        plan=plan,
        candidates=result.candidates,
        cites=cites,
        follow_ups=follow_ups,
        not_found=False,
        retrieval=retrieval,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        started_at=started_at,
    )
    yield _done_frame(message_id, tokens_in, tokens_out, started_at)


# ── 生成环节 ──────────────────────────────────────────────────────────


def _build_messages(
    plan: QueryPlan,
    candidates: list[Candidate],
    history: list[dict[str, str]],
) -> list[ChatMessage]:
    """组装 OpenAI 兼容消息体：系统提示词 → 历史对话 → 参考资料 + 当前问题。

    历史只用于指代理解，因此排在参考资料之前，且不带引用编号。
    """
    messages: list[ChatMessage] = [{"role": "system", "content": prompt.SYSTEM_PROMPT}]
    messages.extend(to_history_messages(history))
    context = prompt.build_context([item.hit for item in candidates])
    messages.append({"role": "user", "content": prompt.build_user_prompt(context, plan.rewritten)})
    return messages


async def _iterate_stream(stream: AsyncIterator[ChatStreamChunk], request: Request) -> AsyncIterator[ChatStreamChunk]:
    """消费 LLM 流，把超时与断连翻译成 ``PipelineError``（帧层只认 code）。

    首片与空闲超时、断连探测策略来自 ``app.core.chat.streaming``，本层只做错误码映射。
    """
    try:
        async for chunk in iter_stream(
            stream,
            first_token_timeout=settings.KB_LLM_FIRST_TOKEN_TIMEOUT,
            idle_timeout=settings.KB_LLM_TIMEOUT,
            is_disconnected=request.is_disconnected,
        ):
            yield chunk
    except StreamTimeoutError as exc:
        raise PipelineError("generationTimeout", prompt.GENERATION_TIMEOUT_MESSAGE) from exc
    except StreamInterruptedError as exc:
        logger.error(f"模型流式生成中断（generationInterrupted）：{exc}")
        raise PipelineError("generationInterrupted", prompt.GENERATION_INTERRUPTED_MESSAGE) from exc


# ── 内部实现 ──────────────────────────────────────────────────────────


async def _resolve_scope(db: AsyncSession, data: ChatStreamInSchema) -> list[int]:
    """解析问答范围：只保留「存在且启用」的空间，scope_type=1 取全部启用空间。"""
    # 直接读知识库的空间清单（停用空间不参与），避免问答域自带一份空间表
    enabled = {int(item["id"]) for item in await space_option_list(db)}
    if data.scope_type == 2:
        return [int(space_id) for space_id in (data.space_ids or []) if int(space_id) in enabled]
    return sorted(enabled)


def _empty_retrieval() -> dict:
    """空检索统计（固定话术与拒答分支用）。"""
    return {"recalled": 0, "reranked": 0, "selected": 0, "threshold": 0.0, "rerankUsed": False}


async def _persist_assistant(
    *,
    session_id: int,
    answer: str,
    plan: QueryPlan,
    candidates: list[Candidate],
    cites: list[CiteItemSchema],
    follow_ups: list[str],
    not_found: bool,
    retrieval: dict,
    tokens_in: int,
    tokens_out: int,
    started_at: float,
) -> int:
    """落库 assistant 消息并累加引用计数，返回消息ID（0=落库失败）。"""
    payload = MessageCreateSchema(
        session_id=session_id,
        role=ROLE_ASSISTANT,
        content=(answer or "").strip() or prompt.NOT_FOUND_MESSAGE,
        analysis={
            "optimized": plan.rewritten,
            "keywords": list(plan.keywords),
            "intent": plan.intent,
            "injection": plan.injection,
            "context": len(candidates),
            "promptVersion": prompt.PROMPT_VERSION,
            "retrieval": retrieval,
        },
        cites=[cite.model_dump(by_alias=True) for cite in cites],
        follow_ups=follow_ups,
        disclaimer=prompt.DISCLAIMER_NOT_FOUND if not_found else prompt.DISCLAIMER_ANSWER,
        not_found=not_found,
        model_name=settings.LLM_MODEL,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        latency_ms=max(0, int((time.monotonic() - started_at) * 1000)),
    )
    message_id = await save_assistant(session_id, payload)
    if cites:
        with suppress(Exception):
            await touch_cited_documents([cite.doc_id for cite in cites])
    return message_id


async def _close_stream(iterator) -> None:
    """尽力关闭 LLM 流（实现来自 ``app.core.chat.streaming``，异常已内部忽略）。"""
    await close_stream(iterator)


def _event(event: str, data: dict) -> dict:
    """构造 SSE 事件（controller 统一序列化为 text/event-stream 帧）。"""
    return {"event": event, "data": data}


def _done_frame(message_id: int, tokens_in: int, tokens_out: int, started_at: float) -> dict:
    """构造 done 帧（永远位于帧流末尾）。"""
    logger.info(
        f"问答结束：messageId={message_id} latency={int((time.monotonic() - started_at) * 1000)}ms "
        f"tokens_in={tokens_in} tokens_out={tokens_out}"
    )
    return _event("done", {"messageId": message_id, "usage": {"tokens_in": tokens_in, "tokens_out": tokens_out}})
