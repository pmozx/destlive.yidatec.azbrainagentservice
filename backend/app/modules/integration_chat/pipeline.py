"""问答链路编排（LangGraph StateGraph）：预处理 → 检索 → 置信 → 生成 → 后处理，产出 SSE 事件流。

节点（见 ``_build_graph``）：
- ``prepare``：校验问题、解析问答范围、建会话与查询计划；只做决策，不产出帧；
- ``fixed_reply``：固定话术（问模型/提示词/用量），不检索、不调模型；
- ``refuse``：闲聊/库外拒答，补一个 retrieving 帧保持帧序；
- ``analyze``：正常链路的预处理帧（analyzing 含改写提示 → retrieving）；
- ``search``：向量检索 + 精排 + 置信三闸，产出候选与检索统计；
- ``not_found``：低置信拒答；
- ``generate``：引用快照 + 模型流式生成，逐字产出 answer 帧；
- ``finalize``：引用编号校验 + 问题拓展，补 sources / followups 并落库；
- ``abort``：故障收尾（error(code) → done），不落 assistant。

为什么用状态图：三处分流（固定话术/拒答/正常）与两条故障路径（检索失败/生成失败）写进边之后，
帧序不再依赖 if 的先后记忆；每个节点只产出自己那一段帧，调整帧序只动边、不动节点。

帧序契约（前端按此消费，改动需同步前端）：
- 正常：reasoning(analyzing) → reasoning(retrieving) → answer×N → sources → followups → done
- 拒答：reasoning(analyzing) → reasoning(retrieving) → error(notFound) → done
- 故障：error(code) → done（不落 assistant，半截答案不进历史）

口径：
1. ``done`` 帧永远在末尾，且带落库后的 ``messageId``；
2. 检索故障与「库里没有」严格区分：前者 retrievalUnavailable，后者 notFound；
3. 客户端断开即停止消费（不再往下写库），避免把半截答案落成历史；
4. 帧一律经 ``get_stream_writer()`` 下发（``stream_mode="custom"``），节点内不直接 yield，
   帧序只由节点执行顺序与节点内调用顺序决定。
"""

import asyncio
import time
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import suppress
from typing import Any, TypedDict

from fastapi import Request
from langgraph.config import get_stream_writer
from langgraph.graph import END, StateGraph
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
from .rerank import Candidate, GradeResult, grade, rerank_candidates, to_candidates
from .retriever import retrieve
from .schema import ChatStreamInSchema, CiteItemSchema
from .session.schema import MessageCreateSchema

# ── 节点与路由名（边与分支都按这些常量连，改名只需改这里） ────────────
NODE_PREPARE = "prepare"
NODE_FIXED_REPLY = "fixed_reply"
NODE_REFUSE = "refuse"
NODE_ANALYZE = "analyze"
NODE_SEARCH = "search"
NODE_NOT_FOUND = "not_found"
NODE_GENERATE = "generate"
NODE_FINALIZE = "finalize"
NODE_ABORT = "abort"

ROUTE_DIRECT = "direct"
ROUTE_REJECT = "reject"
ROUTE_RETRIEVE = "retrieve"
ROUTE_NOT_FOUND = "not_found"
ROUTE_GENERATE = "generate"
ROUTE_FINALIZE = "finalize"
ROUTE_ABORT = "abort"


class ChatState(TypedDict, total=False):
    """一次问答的状态（纯内存，不挂断点，故允许放请求对象与会话）。

    输入：``request`` / ``db`` / ``data`` / ``started_at``；
    中间产物：``question`` / ``allowed_space_ids`` / ``session_id`` / ``history`` / ``plan``
    / ``candidates`` / ``retrieval`` / ``cites`` / ``answer`` / ``follow_ups``
    / ``tokens_in`` / ``tokens_out``；
    流向：``route`` 决定下一个节点，``failure`` 是 ``abort`` 节点要回给前端的错误帧内容。
    """

    request: Request
    db: AsyncSession
    data: ChatStreamInSchema
    started_at: float
    route: str
    failure: dict[str, str]
    question: str
    allowed_space_ids: list[int]
    session_id: int
    history: list[dict[str, str]]
    plan: QueryPlan
    candidates: list[Candidate]
    selection: GradeResult
    retrieval: dict
    cites: list[CiteItemSchema]
    answer: str
    follow_ups: list[str]
    tokens_in: int
    tokens_out: int
    message_id: int


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
    """执行一次问答，产出 ``{"event": str, "data": dict}`` 事件流。

    帧由各节点的 ``get_stream_writer()`` 写入，这里只按 ``stream_mode="custom"`` 透传，
    因此帧序与节点执行顺序严格一致；客户端断开（生成器被关闭）时图随之中止，不会继续落库。
    """
    initial: ChatState = {
        "request": request,
        "db": db,
        "data": data,
        "started_at": time.monotonic(),
        "tokens_in": 0,
        "tokens_out": 0,
    }
    async for frame in _graph().astream(initial, stream_mode="custom"):
        yield frame


# ── 节点 ──────────────────────────────────────────────────────────────


async def _prepare(state: ChatState) -> dict[str, Any]:
    """校验问题 → 解析问答范围 → 会话/历史/user 消息 → 查询计划，给出分流路向。"""
    data = state["data"]
    question = (data.question or "").strip()
    if not question:
        return {"route": ROUTE_ABORT, "failure": {"code": "internalError", "message": "问题不能为空"}}

    # 问答范围：只认「启用的空间」，指定空间必须落在其中
    allowed_space_ids = await _resolve_scope(state["db"], data)
    if not allowed_space_ids:
        logger.info(f"问答无可用空间：scope_type={data.scope_type} space_ids={data.space_ids}")
        return {"route": ROUTE_ABORT, "failure": {"code": "noSpace", "message": prompt.SPACE_SCOPE_MESSAGE}}

    # 会话 + 历史 + user 消息（独立事务先落库）
    try:
        session_id, history = await prepare_turn(
            session_id=data.session_id,
            question=question,
            scope_type=data.scope_type,
            space_ids=data.space_ids,
            turns=settings.KB_HISTORY_TURNS,
        )
    except ValueError as exc:
        return {"route": ROUTE_ABORT, "failure": {"code": "internalError", "message": str(exc)}}

    plan = build_query_plan(question, allowed_space_ids, history)
    if plan.is_direct:
        route = ROUTE_DIRECT
    elif plan.is_reject:
        route = ROUTE_REJECT
    else:
        route = ROUTE_RETRIEVE
    return {
        "question": question,
        "allowed_space_ids": allowed_space_ids,
        "session_id": session_id,
        "history": history,
        "plan": plan,
        "route": route,
    }


async def _fixed_reply(state: ChatState) -> dict[str, Any]:
    """固定话术（问模型/提示词/用量）：不检索、不调 LLM，仍按契约走 analyzing → answer → done。"""
    writer = get_stream_writer()
    writer(_event("reasoning", {"stage": "analyzing", "text": prompt.ANALYZING_TEXT}))
    writer(_event("answer", {"delta": prompt.SYSTEM_META_MESSAGE}))
    message_id = await _persist_assistant(
        session_id=state["session_id"],
        answer=prompt.SYSTEM_META_MESSAGE,
        plan=state["plan"],
        candidates=[],
        cites=[],
        follow_ups=[],
        not_found=False,
        retrieval=_empty_retrieval(),
        tokens_in=0,
        tokens_out=0,
        started_at=state["started_at"],
    )
    writer(_done_frame(message_id, 0, 0, state["started_at"]))
    return {"message_id": message_id}


async def _refuse(state: ChatState) -> dict[str, Any]:
    """拒答分支（闲聊/库外）：补一个 retrieving 帧保持帧序，不调外部服务。"""
    writer = get_stream_writer()
    writer(_event("reasoning", {"stage": "analyzing", "text": prompt.ANALYZING_TEXT}))
    writer(_event("reasoning", {"stage": "retrieving", "text": prompt.CHITCHAT_MESSAGE}))
    writer(_event("error", {"code": "notFound", "message": prompt.NOT_FOUND_MESSAGE}))
    message_id = await _persist_assistant(
        session_id=state["session_id"],
        answer=prompt.NOT_FOUND_MESSAGE,
        plan=state["plan"],
        candidates=[],
        cites=[],
        follow_ups=[],
        not_found=True,
        retrieval=_empty_retrieval(),
        tokens_in=0,
        tokens_out=0,
        started_at=state["started_at"],
    )
    writer(_done_frame(message_id, 0, 0, state["started_at"]))
    return {"message_id": message_id}


async def _analyze(state: ChatState) -> dict[str, Any]:
    """预处理帧立即下发，不等检索：首字不被检索耗时拖住。"""
    writer = get_stream_writer()
    plan = state["plan"]
    analyzing: dict = {"stage": "analyzing", "text": prompt.ANALYZING_TEXT}
    if plan.rewritten and plan.rewritten != plan.normalized:
        analyzing["optimized"] = plan.rewritten
    writer(_event("reasoning", analyzing))
    logger.info(
        f"问答预处理：session={state['session_id']} intent={plan.intent} "
        f"injection={plan.injection} keywords={plan.keywords[:5]}"
    )
    writer(
        _event(
            "reasoning",
            {
                "stage": "retrieving",
                "text": prompt.RETRIEVING_TEXT.format(space_count=len(state["allowed_space_ids"]), mode="向量检索"),
            },
        )
    )
    return {}


async def _search(state: ChatState) -> dict[str, Any]:
    """检索 + 精排 + 置信三闸；检索故障与「库里没有」在此分流，绝不混为一谈。"""
    plan = state["plan"]
    session_id = state["session_id"]
    try:
        async with asyncio.timeout(settings.EMBEDDING_TIMEOUT + 30):
            hits = await retrieve(state["db"], plan.rewritten, space_ids=state["allowed_space_ids"])
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 检索异常一律「服务不可用」，不能误判为库里没有
        logger.error(f"知识检索失败（retrievalUnavailable）：session={session_id} {exc}")
        return {
            "route": ROUTE_ABORT,
            "failure": {"code": "retrievalUnavailable", "message": prompt.RETRIEVAL_UNAVAILABLE_MESSAGE},
        }

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
        return {"route": ROUTE_NOT_FOUND, "retrieval": retrieval, "candidates": [], "selection": result}
    return {
        "route": ROUTE_GENERATE,
        "retrieval": retrieval,
        "candidates": result.candidates,
        "selection": result,
    }


async def _not_found(state: ChatState) -> dict[str, Any]:
    """置信不足拒答：属业务结果，照常落 assistant（not_found=True）。"""
    writer = get_stream_writer()
    writer(_event("error", {"code": "notFound", "message": prompt.NOT_FOUND_MESSAGE}))
    message_id = await _persist_assistant(
        session_id=state["session_id"],
        answer=prompt.NOT_FOUND_MESSAGE,
        plan=state["plan"],
        candidates=[],
        cites=[],
        follow_ups=[],
        not_found=True,
        retrieval=state["retrieval"],
        tokens_in=0,
        tokens_out=0,
        started_at=state["started_at"],
    )
    writer(_done_frame(message_id, 0, 0, state["started_at"]))
    return {"message_id": message_id}


async def _generate(state: ChatState) -> dict[str, Any]:
    """引用快照先建好（正文里的 [n] 才有意义），再逐字下发 answer 帧。"""
    writer = get_stream_writer()
    plan = state["plan"]
    candidates = state["candidates"]
    session_id = state["session_id"]
    cites = build_cites(candidates, keywords=plan.keywords)
    messages = _build_messages(plan, candidates, state.get("history") or [])
    answer_parts: list[str] = []
    tokens_in = tokens_out = 0
    stream = LlmClient(timeout=settings.KB_LLM_TIMEOUT).stream_chat_events(
        messages,
        temperature=0.0 if plan.injection else settings.KB_ANSWER_TEMPERATURE,
        max_tokens=settings.KB_ANSWER_MAX_TOKENS,
    )
    try:
        async for chunk in _iterate_stream(stream, state["request"]):
            if chunk.delta:
                answer_parts.append(chunk.delta)
                writer(_event("answer", {"delta": chunk.delta}))
            # 用量只在末帧给全：有值就覆盖，无值沿用
            tokens_in = chunk.tokens_in or tokens_in
            tokens_out = chunk.tokens_out or tokens_out
    except PipelineError as exc:
        logger.warning(f"问答链路中断（{exc.code}）：session={session_id} {exc.message}")
        return {
            "route": ROUTE_ABORT,
            "failure": {"code": exc.code, "message": exc.message},
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
        }
    finally:
        await _close_stream(stream)

    answer = "".join(answer_parts).strip()
    if not answer:
        logger.warning(f"模型返回空正文（generationInterrupted）：session={session_id}")
        return {
            "route": ROUTE_ABORT,
            "failure": {"code": "generationInterrupted", "message": prompt.GENERATION_INTERRUPTED_MESSAGE},
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
        }
    return {
        "route": ROUTE_FINALIZE,
        "cites": cites,
        "answer": answer,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
    }


async def _finalize(state: ChatState) -> dict[str, Any]:
    """后处理：引用编号校验 → 问题拓展 → sources / followups 帧 → 落库 → done。"""
    writer = get_stream_writer()
    answer = state["answer"]
    cites = state["cites"]
    cleaned, invalid = sanitize_citations(answer, len(cites))
    if invalid:
        logger.warning(f"正文引用编号越界已摘除：session={state['session_id']} invalid={invalid} cite_count={len(cites)}")
    answer = cleaned.strip() or answer
    follow_ups = build_followups(state["candidates"], state["question"])

    writer(_event("sources", {"cites": [cite.model_dump(by_alias=True) for cite in cites]}))
    if follow_ups:
        writer(_event("followups", {"questions": follow_ups}))

    message_id = await _persist_assistant(
        session_id=state["session_id"],
        answer=answer,
        plan=state["plan"],
        candidates=state["candidates"],
        cites=cites,
        follow_ups=follow_ups,
        not_found=False,
        retrieval=state["retrieval"],
        tokens_in=state["tokens_in"],
        tokens_out=state["tokens_out"],
        started_at=state["started_at"],
    )
    writer(_done_frame(message_id, state["tokens_in"], state["tokens_out"], state["started_at"]))
    return {"message_id": message_id}


async def _abort(state: ChatState) -> dict[str, Any]:
    """故障收尾：只回 error + done，不落 assistant（半截答案不进历史）。"""
    writer = get_stream_writer()
    failure = state.get("failure") or {"code": "internalError", "message": prompt.INTERNAL_ERROR_MESSAGE}
    writer(_event("error", failure))
    writer(_done_frame(0, 0, 0, state["started_at"]))
    return {}


# ── 图装配 ────────────────────────────────────────────────────────────


def _route(state: ChatState) -> str:
    """取下一个节点名（各节点把自己的路向写在 ``route`` 上）。"""
    return state.get("route") or ROUTE_ABORT


def _build_graph():
    """装配问答状态图（无断点：一次请求一张图，跑完即弃，不值得落盘）。"""
    graph = StateGraph(ChatState)
    graph.add_node(NODE_PREPARE, _prepare)
    graph.add_node(NODE_FIXED_REPLY, _fixed_reply)
    graph.add_node(NODE_REFUSE, _refuse)
    graph.add_node(NODE_ANALYZE, _analyze)
    graph.add_node(NODE_SEARCH, _search)
    graph.add_node(NODE_NOT_FOUND, _not_found)
    graph.add_node(NODE_GENERATE, _generate)
    graph.add_node(NODE_FINALIZE, _finalize)
    graph.add_node(NODE_ABORT, _abort)

    graph.set_entry_point(NODE_PREPARE)
    graph.add_conditional_edges(
        NODE_PREPARE,
        _route,
        {
            ROUTE_DIRECT: NODE_FIXED_REPLY,
            ROUTE_REJECT: NODE_REFUSE,
            ROUTE_RETRIEVE: NODE_ANALYZE,
            ROUTE_ABORT: NODE_ABORT,
        },
    )
    graph.add_edge(NODE_ANALYZE, NODE_SEARCH)
    graph.add_conditional_edges(
        NODE_SEARCH,
        _route,
        {
            ROUTE_GENERATE: NODE_GENERATE,
            ROUTE_NOT_FOUND: NODE_NOT_FOUND,
            ROUTE_ABORT: NODE_ABORT,
        },
    )
    graph.add_conditional_edges(
        NODE_GENERATE,
        _route,
        {ROUTE_FINALIZE: NODE_FINALIZE, ROUTE_ABORT: NODE_ABORT},
    )
    for terminal in (NODE_FIXED_REPLY, NODE_REFUSE, NODE_NOT_FOUND, NODE_FINALIZE, NODE_ABORT):
        graph.add_edge(terminal, END)
    return graph.compile()


_GRAPH = None


def _graph():
    """惰性编译并缓存状态图（编译只做一次，节点内不含请求级依赖）。"""
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = _build_graph()
    return _GRAPH


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
