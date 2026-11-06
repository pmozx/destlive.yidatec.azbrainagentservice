"""问答编排服务（#10 历史 / #14 生成）：检索 → 组装 → 生成 → 落库。

四道必须守住的边界：

1. **归属先于一切**：会话按 ``session_id + user_id`` 定位，不属于自己与不存在返回同一个
   结果（上层转 404），不泄漏他人会话的存在性。
2. **外部调用不占数据库事务**：向量化、检索、模型生成都在写事务之外完成。一次生成可能
   几十秒，把它包进事务会长期占用连接与行锁。
3. **迟到必丢弃**：生成前记下会话状态快照，写回时用行锁重读；期间被删除或清空就抛
   :class:`ChatTurnDiscardedError`（契约 409 ``RESULT_EXPIRED``），绝不把会话写活。
4. **不可验证的引用不返回**：回查不到切片原文（切片已删）或没有足够证据时不返回
   citations / source_snapshots，宁可真话「没找到」，也不给 GSI 一份校验不过的来源清单。

两种出口共用同一套编排：``completion`` 一次性返回（契约 #14 / 管理端 /ask），
``completion_stream`` 逐帧返回（管理端 /ask/stream）。差别只有「怎么把答案交给调用方」——
检索、拒答、引用、落库、迟到丢弃全部是同一份代码，不存两套口径。
"""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

from fastapi import status as http_status

from app.config.setting import settings
from app.core.chat.cite import sanitize_citations
from app.core.chat.judge import judge_sufficiency
from app.core.chat.streaming import (
    TICK,
    StreamInterruptedError,
    StreamTimeoutError,
    close_stream,
    iter_stream,
    iter_with_heartbeat,
)
from app.core.clients import get_llm_client
from app.core.database import async_session_factory
from app.core.exceptions import CustomException
from app.core.logger import logger
from app.core.observability import RUN_GENERATE, RUN_RETRIEVE, RUN_TURN, llm_config, trace_metadata, traced
from app.utils.cursor import decode_cursor, encode_cursor

from ..chat_metric.meter import ERR_CLIENT_DISCONNECTED, MODE_STREAM, MODE_SYNC, TurnMeter
from ..chat_message.crud import KbChatMessageCRUD
from ..chat_message.model import KbChatMessageModel
from ..chat_message.schema import MESSAGE_ACTIVE, MESSAGE_ASSISTANT, MESSAGE_USER, MessageRecord
from ..chat_session.crud import KbChatSessionCRUD
from ..chat_session.schema import DEFAULT_SESSION_TITLE, SESSION_ACTIVE, SESSION_CLEARED, SESSION_DELETED, derive_title
from ..chat_session.service import ChatSessionNotFoundError
from ..publication.crud import KbPublicationCRUD
from ..retrieval.schema import SearchQuery
from ..retrieval.service import KbRetrievalService, RetrievalAuthorizationUnavailable, RetrievalEmbeddingUnavailable
from . import sse
from .crud import ChunkKey, KbChatEvidenceCRUD
from .prompt import build_messages, history_turns, recall_block
from .schema import (
    FINISH_NO_EVIDENCE,
    FINISH_STOP,
    NO_EVIDENCE_ANSWER,
    REASON_SOURCE_UNAVAILABLE,
    ChatEvidence,
    ChatTurn,
    HistoryMessage,
    HistoryPage,
)


def _elapsed_ms(started: float) -> int:
    """帧与出参里的耗时统一为毫秒整数（进度帧、``done`` 帧、非流式出参同一口径）。"""
    return round((perf_counter() - started) * 1000)


class ChatTurnDiscardedError(Exception):
    """生成期间会话被删除或清空：本轮回答必须丢弃，不能把会话写活。"""


@dataclass(frozen=True)
class _Snapshot:
    """生成前的会话快照：只需要「当时的状态」和「当时的合规历史」。"""

    status: str
    history: tuple[tuple[str, str], ...]


class KbChatService:
    """问答编排服务（无请求级会话依赖，自己开事务）。"""

    def __init__(self, *, user_id: int, tenant_id: str) -> None:
        self.user_id = user_id
        self.tenant_id = tenant_id

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # §一  生成（#14）
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    @traced(RUN_TURN, tags=("kb", "qa", MODE_SYNC))
    async def completion(
        self,
        *,
        session_id: str,
        question: str,
        prompt_text: str,
        language: str = "zh-CN",
        max_tokens: int | None = None,
    ) -> ChatTurn:
        """一轮问答：检索 → 生成（或拒答）→ 双消息落库 → 刷新会话活跃度。"""
        meter = TurnMeter(
            session_id=session_id,
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            mode=MODE_SYNC,
            model=get_llm_client().model,
        )
        meter.prompt(prompt_text)
        meter.annotate()

        snapshot = await self._snapshot(session_id)
        evidences = await self._retrieve(question, meter=meter)
        if await self._insufficient(question, evidences):
            # 二判判为「资料不足」：当成无证据走拒答，不拿半相关材料硬答
            evidences = []

        if not self._has_evidence(evidences):
            logger.info("问答无证据：user_id={} session={} 命中={}", self.user_id, session_id, len(evidences))
            answer, no_evidence, finish_reason = NO_EVIDENCE_ANSWER, True, FINISH_NO_EVIDENCE
            citations: tuple[dict[str, Any], ...] = ()
            snapshots: tuple[dict[str, Any], ...] = ()
            evidences = []
        else:
            answer = await self._generate(
                question=question,
                prompt_text=prompt_text,
                language=language,
                evidences=evidences,
                history=snapshot.history,
                max_tokens=max_tokens,
                meter=meter,
            )
            no_evidence, finish_reason = False, FINISH_STOP
            citations = tuple(evidence.to_citation() for evidence in evidences)
            snapshots = tuple(evidence.to_snapshot() for evidence in evidences)

        user_message_id, assistant_message_id = await self._persist(
            session_id=session_id,
            prior_status=snapshot.status,
            question=question,
            answer=answer,
            citations=citations,
            evidences=evidences,
            meter=meter,
        )
        await meter.save(no_evidence=no_evidence, finish_reason=finish_reason)
        return ChatTurn(
            session_id=session_id,
            user_message_id=user_message_id,
            assistant_message_id=assistant_message_id,
            answer=answer,
            citations=citations,
            source_snapshots=snapshots,
            no_evidence=no_evidence,
            finish_reason=finish_reason,
        )

    @traced(RUN_TURN, tags=("kb", "qa", MODE_STREAM))
    async def completion_stream(
        self,
        *,
        session_id: str,
        question: str,
        prompt_text: str,
        request_id: str,
        language: str = "zh-CN",
        max_tokens: int | None = None,
        is_disconnected: Callable[[], Awaitable[bool]] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """同 ``completion`` 的一轮问答，改为逐帧交给调用方（SSE）。

        帧序：``meta`` → ``stage(retrieving)`` → [ping]* → ``stage(generating)`` → ``delta``*
        → ``citations`` → ``done``；失败则以 ``error`` 收尾。三条不变式：

        1. ``meta`` 第一帧，``done``/``error`` 唯一且最后一帧；
        2. **落库在 citations 之后、done 之前**：事务失败则一条不写并发 ``error``，
           不允许「半截答案入库」给下一轮带来没头没尾的上下文；
        3. **客户端断开立即停**（``iter_stream`` 断连探测）：不落库、不发帧，避免继续烧 token。

        超时不是「等得久」而是三种：首字超时、两片间的空闲超时、整轮总预算（`KB_LLM_TIMEOUT`）。
        预算以绝对 deadline 传给每个等待点，**不用 ``asyncio.timeout`` 包住 ``yield``**——
        生成器在 yield 处挂起，墙钟会把慢客户端写数据的时间也算进去。
        """
        loop = asyncio.get_running_loop()
        started = perf_counter()
        meter = TurnMeter(
            session_id=session_id,
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            mode=MODE_STREAM,
            model=get_llm_client().model,
        )
        meter.prompt(prompt_text)
        meter.annotate()
        deadline = loop.time() + settings.KB_LLM_TIMEOUT
        yield sse.meta_event(session_id=session_id, request_id=request_id)

        try:
            snapshot = await self._snapshot(session_id)

            yield sse.stage_event(phase=sse.PHASE_RETRIEVING, elapsed_ms=_elapsed_ms(started))
            evidences: list[ChatEvidence] = []
            # 检索没有增量帧，但可能跑好几秒：期间用心跳占住连接（网关 30–60s 就断空闲连接）
            async for item in iter_with_heartbeat(
                self._retrieve_in_budget(question, deadline, meter=meter),
                interval=settings.KB_CHAT_STREAM_PING_INTERVAL,
            ):
                if item is TICK:
                    yield sse.ping_event(elapsed_ms=_elapsed_ms(started))
                else:
                    evidences = item.value

            if await self._insufficient(question, evidences):
                # 同非流式：判为资料不足就走拒答分支（帧序不变）
                evidences = []
            if self._has_evidence(evidences):
                yield sse.stage_event(phase=sse.PHASE_GENERATING, elapsed_ms=_elapsed_ms(started))
                parts: list[str] = []
                async for frame in self._delta_frames(
                    question=question,
                    prompt_text=prompt_text,
                    language=language,
                    evidences=evidences,
                    history=snapshot.history,
                    max_tokens=max_tokens,
                    deadline=deadline,
                    is_disconnected=is_disconnected,
                    parts=parts,
                    meter=meter,
                ):
                    yield frame
                # 已发出的字节收不回来：越界引用编号与多余空白在落库前统一清洗，
                # 终稿随 done 帧下发，客户端以它为准覆盖渲染（规范 S14）。
                answer, invalid = sanitize_citations("".join(parts), len(evidences))
                meter.invalid_citations = len(invalid)
                if invalid:
                    logger.warning("模型引用了不存在的证据编号，已摘除：user_id={} invalid={}", self.user_id, invalid)
                answer = answer.strip() or NO_EVIDENCE_ANSWER
                citations: tuple[dict[str, Any], ...] = tuple(evidence.to_citation() for evidence in evidences)
                no_evidence, finish_reason = False, FINISH_STOP
            else:
                logger.info("问答无证据（流式）：user_id={} session={} 命中={}", self.user_id, session_id, len(evidences))
                evidences = []
                answer, no_evidence, finish_reason = NO_EVIDENCE_ANSWER, True, FINISH_NO_EVIDENCE
                citations = ()
                # 拒答也走 delta：前端只需要「渲染正文」，不必为拒答写分支
                yield sse.delta_event(seq=1, text=answer)

            yield sse.citations_event(citations=citations)

            user_message_id, assistant_message_id = await self._persist(
                session_id=session_id,
                prior_status=snapshot.status,
                question=question,
                answer=answer,
                citations=citations,
                evidences=evidences,
                meter=meter,
            )
            await meter.save(no_evidence=no_evidence, finish_reason=finish_reason)
            yield sse.done_event(
                session_id=session_id,
                user_message_id=user_message_id,
                assistant_message_id=assistant_message_id,
                answer=answer,
                no_evidence=no_evidence,
                finish_reason=finish_reason,
                elapsed_ms=_elapsed_ms(started),
            )
        except ChatSessionNotFoundError as exc:
            await meter.fail(sse.ERR_SESSION_NOT_FOUND)
            yield sse.error_event(
                code=sse.ERR_SESSION_NOT_FOUND, message=str(exc), retryable=False, session_id=session_id
            )
        except ChatTurnDiscardedError as exc:
            await meter.fail(sse.ERR_TURN_DISCARDED)
            yield sse.error_event(code=sse.ERR_TURN_DISCARDED, message=str(exc), retryable=False, session_id=session_id)
        except (RetrievalAuthorizationUnavailable, RetrievalEmbeddingUnavailable) as exc:
            # 与 #07 同一口径：依赖故障如实报错，不拿空 citations 冒充「没有依据」
            logger.warning("问答依赖不可用（流式）：user_id={} session={} {}", self.user_id, session_id, exc)
            await meter.fail(sse.ERR_DEPENDENCY_UNAVAILABLE)
            yield sse.error_event(
                code=sse.ERR_DEPENDENCY_UNAVAILABLE, message=str(exc), retryable=True, session_id=session_id
            )
        except (StreamTimeoutError, TimeoutError) as exc:
            logger.warning("问答超时（流式）：user_id={} session={} {}", self.user_id, session_id, exc)
            await meter.fail(sse.ERR_TIMEOUT)
            yield sse.error_event(
                code=sse.ERR_TIMEOUT,
                message="回答超时，请稍后重试，或把问题问得更具体一些。",
                retryable=True,
                session_id=session_id,
            )
        except StreamInterruptedError as exc:
            if is_disconnected is not None and await is_disconnected():
                # 客户端已经走了：没人接收错误帧，只留日志（本轮一字未落库）
                logger.warning("客户端已断开，本轮问答作废（未落库）：user_id={} session={}", self.user_id, session_id)
                await meter.fail(ERR_CLIENT_DISCONNECTED)
                return
            logger.error("模型流式生成中断：user_id={} session={} {}", self.user_id, session_id, exc)
            await meter.fail(sse.ERR_MODEL_FAILED)
            yield sse.error_event(
                code=sse.ERR_MODEL_FAILED, message="模型服务异常，请稍后重试。", retryable=True, session_id=session_id
            )
        except CustomException as exc:
            # 模型客户端的可读异常：503=未配置/依赖不可用，502=上游失败，其余按内部错误
            if exc.status_code == http_status.HTTP_503_SERVICE_UNAVAILABLE:
                code = sse.ERR_DEPENDENCY_UNAVAILABLE
            elif exc.status_code == http_status.HTTP_502_BAD_GATEWAY:
                code = sse.ERR_MODEL_FAILED
            else:
                code = sse.ERR_INTERNAL
            logger.warning(
                "问答失败（流式）：user_id={} session={} status={} {}", self.user_id, session_id, exc.status_code, exc
            )
            await meter.fail(code)
            yield sse.error_event(code=code, message=exc.msg, retryable=True, session_id=session_id)
        except Exception:
            logger.exception("问答未知异常（流式）：user_id={} session={}", self.user_id, session_id)
            await meter.fail(sse.ERR_INTERNAL)
            yield sse.error_event(
                code=sse.ERR_INTERNAL, message="服务内部错误，请稍后重试。", retryable=True, session_id=session_id
            )

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # §二  历史（#10）
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def history(self, *, session_id: str, cursor: str | None, limit: int) -> HistoryPage:
        """按时间正序分页，并逐条判定依赖是否已失效（失效整条隐藏正文与引用）。"""
        async with async_session_factory() as db:
            session = await KbChatSessionCRUD(db).get_owned(session_id=session_id, user_id=self.user_id)
            if session is None:
                raise ChatSessionNotFoundError("会话不存在")
            offset = decode_cursor(cursor)
            rows = await KbChatMessageCRUD(db).list_visible(session_id=session_id, offset=offset, limit=limit + 1)

        has_next = len(rows) > limit
        rows = rows[:limit]
        visibility = await self.dependency_visibility([row.knowledge_deps or [] for row in rows])
        items = tuple(
            HistoryMessage(
                record=self.to_record(row),
                content_visible=visible,
                reason=None if visible else REASON_SOURCE_UNAVAILABLE,
            )
            for row, visible in zip(rows, visibility, strict=True)
        )
        return HistoryPage(items=items, next_cursor=encode_cursor(offset + limit) if has_next else None)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # §三  可见性（#10 / #15 共用）
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def dependency_visibility(self, batches: Sequence[Sequence[dict[str, Any]]]) -> list[bool]:
        """批量判定「消息的知识依赖是否仍全部有效」。

        一个依赖有效的充要条件：发布单元仍在授权范围内，且当前生效快照仍是当时那版。
        任一条件不满足即整条消息隐藏（契约 FR-RAG-08）——部分引用失效时，剩下的片段
        已不足以支撑原答案，只藏一条引用反而会误导。
        """
        publication_ids = {
            str(dep.get("publication_id") or "")
            for batch in batches
            for dep in batch
            if str(dep.get("publication_id") or "")
        }
        if not publication_ids:
            # 没有知识依赖（用户消息 / 拒答消息）：本来就无需授权，直接可见
            return [True] * len(batches)

        authorized = await KbRetrievalService().authorized_publication_ids(str(self.user_id))
        pointers: dict[str, str | None] = {}
        if authorized:
            async with async_session_factory() as db:
                pointers = await KbPublicationCRUD(db).effective_pointers(publication_ids & authorized)

        results: list[bool] = []
        for batch in batches:
            visible = True
            for dep in batch:
                publication_id = str(dep.get("publication_id") or "")
                record_id = str(dep.get("record_id") or "")
                if publication_id not in authorized or pointers.get(publication_id) != record_id:
                    visible = False
                    break
            results.append(visible)
        return results

    async def owns_session(self, session_id: str, *, include_deleted: bool = False) -> bool:
        """会话是否属于本用户（#15 结果查询用，判定前先收窄归属）。

        ``include_deleted=True`` 供删除类操作的结果查询：会话已软删，但「这条请求是不是
        我自己的」仍必须能判定，否则删完就查不到自己的操作结果（也给了猜 ID 探测的口子）。
        """
        async with async_session_factory() as db:
            crud = KbChatSessionCRUD(db)
            if include_deleted:
                row = await crud.get_by_session_id(session_id)
                return row is not None and row.user_id == self.user_id
            return await crud.get_owned(session_id=session_id, user_id=self.user_id) is not None

    async def message(self, message_id: str) -> MessageRecord | None:
        """按 ``message_id`` 取一条消息（#15 复核答案是否仍可见）。"""
        async with async_session_factory() as db:
            row = await KbChatMessageCRUD(db).get_by_message_id(message_id)
        return None if row is None else self.to_record(row)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # §四  内部步骤
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def _snapshot(self, session_id: str) -> _Snapshot:
        """生成前快照：同时完成归属校验与历史读取，省掉一次往返。"""
        async with async_session_factory() as db:
            session = await KbChatSessionCRUD(db).get_owned(session_id=session_id, user_id=self.user_id)
            if session is None:
                raise ChatSessionNotFoundError("会话不存在")
            rows = await KbChatMessageCRUD(db).recent_visible(
                session_id=session_id,
                limit=settings.KB_HISTORY_TURNS,
            )
        return _Snapshot(
            status=session.status or SESSION_ACTIVE,
            history=tuple((row.role, row.content) for row in rows),
        )

    @traced(RUN_RETRIEVE, tags=("kb", "retrieval"))
    async def _retrieve(self, question: str, *, meter: TurnMeter) -> list[ChatEvidence]:
        """自然语言通道检索 → 回查切片原文 → 组装为带编号的证据。

        问答只能用自然语言通道：提问是整句而不是关键词，纯字面命中会把「怎么申请 VPN」
        这种问法漏掉。向量化不可用时**不降级**成关键词检索（契约 #07 同一口径），
        交给上层按依赖故障处理。耗时与证据条数顺手记进 ``meter``（指标表与 trace 同源）。
        """
        started = perf_counter()
        result = await KbRetrievalService().natural_language_search(
            SearchQuery(
                query=question,
                tenant_id=self.tenant_id,
                user_id=str(self.user_id),
                limit=max(1, settings.KB_RERANK_TOP_N),
            )
        )
        hits = list(result.hits)
        if not hits:
            meter.retrieve_ms = _elapsed_ms(started)
            return []

        keys = [
            ChunkKey(publication_id=hit.publication_id, record_id=hit.record_id, chunk_id=hit.chunk_id) for hit in hits
        ]
        async with async_session_factory() as db:
            contents = await KbChatEvidenceCRUD(db).chunk_contents(keys)

        quote_limit = max(1, settings.KB_SNIPPET_CHARS)
        evidences: list[ChatEvidence] = []
        for hit, key in zip(hits, keys, strict=True):
            # 完整切片正文进上下文（``content``），截断片段只用于引用回放（``quote``）
            content = (contents.get(key) or "").strip()
            if not content:
                # 切片已被删除或正文为空：引用无法逐字校验，直接剔除而不是留个空引用
                logger.warning(
                    "证据切片回查不到正文，已剔除：publication={} record={} chunk={}",
                    hit.publication_id,
                    hit.record_id,
                    hit.chunk_id,
                )
                continue
            evidences.append(
                ChatEvidence(
                    index=len(evidences) + 1,
                    resource_type=hit.resource_type,
                    resource_id=hit.resource_id,
                    publication_id=hit.publication_id,
                    record_id=hit.record_id,
                    version_id=hit.version_id,
                    revision=hit.revision,
                    chunk_id=hit.chunk_id,
                    quote=content[:quote_limit],
                    content=content,
                    title=hit.title,
                    score=hit.score,
                )
            )
        meter.evidences([evidence.score for evidence in evidences])
        meter.retrieve_ms = _elapsed_ms(started)
        return evidences

    @staticmethod
    def _has_evidence(evidences: Sequence[ChatEvidence]) -> bool:
        """证据够不够：少于 ``KB_GRADE_MIN_CHUNKS`` 直接拒答（流式与非流式同一口径）。"""
        return len(evidences) >= max(1, settings.KB_GRADE_MIN_CHUNKS)

    async def _insufficient(self, question: str, evidences: Sequence[ChatEvidence]) -> bool:
        """灰区二判：条数与绝对分闸过关、但最高相关度偏低时，让模型判一次「资料够不够」。

        只在 ``KB_GRADE_JUDGE_ENABLE`` 打开且 ``top_score < KB_GRADE_JUDGE_TOP_SCORE`` 时
        才多花一次模型调用；判定失败按「够答」处理（``judge_sufficiency`` 内部已降级），
        给不出结论时宁可按原样回答，也不因为质检异常而拒答。
        """
        if not settings.KB_GRADE_JUDGE_ENABLE or not evidences:
            return False
        top_score = max((evidence.score for evidence in evidences), default=0.0)
        if top_score >= settings.KB_GRADE_JUDGE_TOP_SCORE:
            return False
        sufficient, missing = await judge_sufficiency(question, recall_block(evidences))
        trace_metadata(grade_judge="sufficient" if sufficient else "insufficient", grade_top_score=round(top_score, 4))
        if sufficient:
            return False
        # 证据条数保留检索时那份（没进上下文），失败/拒答轮都靠 top_score 与这个标记区分
        logger.info(
            "证据二判判为资料不足，转拒答：user_id={} 相关度={} 缺={}",
            self.user_id,
            round(top_score, 4),
            missing or "未说明",
        )
        return True

    async def _retrieve_in_budget(self, question: str, deadline: float, *, meter: TurnMeter) -> list[ChatEvidence]:
        """检索也受整轮预算约束：剩余时间不足时立即超时，而不是拖到生成阶段才发现没预算。"""
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise StreamTimeoutError("检索尚未开始就已耗尽本轮总预算")
        return await asyncio.wait_for(self._retrieve(question, meter=meter), remaining)

    async def _delta_frames(
        self,
        *,
        question: str,
        prompt_text: str,
        language: str,
        evidences: Sequence[ChatEvidence],
        history: Sequence[tuple[str, str]],
        max_tokens: int | None,
        deadline: float,
        is_disconnected: Callable[[], Awaitable[bool]] | None,
        parts: list[str],
        meter: TurnMeter,
    ) -> AsyncIterator[dict[str, Any]]:
        """生成阶段的帧：逐片转发 ``delta``，正文按序累积到 ``parts`` 里供落库。

        与 ``_generate`` 的差别只有「拿到答案的方式」：一个收齐再清洗，一个边收边发；
        引用编号清洗、空回答兜底、温度与 token 上限完全一致。
        """
        messages = build_messages(
            prompt_text=prompt_text,
            language=language,
            evidences=evidences,
            history=history_turns(history),
            question=question,
        )
        started = perf_counter()
        stream = get_llm_client().stream_chat_events(
            messages,
            temperature=settings.KB_ANSWER_TEMPERATURE,
            max_tokens=max_tokens or settings.KB_ANSWER_MAX_TOKENS,
            config=llm_config(RUN_GENERATE, mode=MODE_STREAM, evidence_count=len(evidences)),
        )
        seq = 0
        tokens_in = tokens_out = 0
        try:
            async for chunk in iter_stream(
                stream,
                first_token_timeout=settings.KB_LLM_FIRST_TOKEN_TIMEOUT,
                idle_timeout=settings.KB_CHAT_STREAM_IDLE_TIMEOUT,
                is_disconnected=is_disconnected,
                deadline=deadline,
            ):
                # 用量只在末片给全（stream_usage=True），有值就覆盖、无值就沿用
                tokens_in = chunk.tokens_in or tokens_in
                tokens_out = chunk.tokens_out or tokens_out
                if not chunk.delta:
                    continue
                if meter.first_token_ms is None:
                    meter.first_token_ms = _elapsed_ms(started)
                parts.append(chunk.delta)
                seq += 1
                yield sse.delta_event(seq=seq, text=chunk.delta)
        finally:
            meter.generate_ms = _elapsed_ms(started)
            meter.tokens_in, meter.tokens_out = tokens_in, tokens_out
            # 超时/断连时上游生成器可能还在跑：不关掉会继续烧 token
            await close_stream(stream)
        logger.info(
            "流式生成完成：user_id={} 分片={} tokens_in={} tokens_out={}", self.user_id, seq, tokens_in, tokens_out
        )

    async def _generate(
        self,
        *,
        question: str,
        prompt_text: str,
        language: str,
        evidences: Sequence[ChatEvidence],
        history: Sequence[tuple[str, str]],
        max_tokens: int | None,
        meter: TurnMeter,
    ) -> str:
        """调用模型并清理越界引用编号（编号与证据一一对应，只摘除不重排）。"""
        started = perf_counter()
        messages = build_messages(
            prompt_text=prompt_text,
            language=language,
            evidences=evidences,
            history=history_turns(history),
            question=question,
        )
        answer = await get_llm_client().chat(
            messages,
            temperature=settings.KB_ANSWER_TEMPERATURE,
            max_tokens=max_tokens or settings.KB_ANSWER_MAX_TOKENS,
            config=llm_config(RUN_GENERATE, mode=MODE_SYNC, evidence_count=len(evidences)),
        )
        meter.generate_ms = _elapsed_ms(started)
        cleaned, invalid = sanitize_citations(answer, len(evidences))
        meter.invalid_citations = len(invalid)
        if invalid:
            logger.warning("模型引用了不存在的证据编号，已摘除：user_id={} invalid={}", self.user_id, invalid)
        return cleaned.strip() or NO_EVIDENCE_ANSWER

    async def _persist(
        self,
        *,
        session_id: str,
        prior_status: str,
        question: str,
        answer: str,
        citations: tuple[dict[str, Any], ...],
        evidences: Sequence[ChatEvidence],
        meter: TurnMeter,
    ) -> tuple[str, str]:
        """写回用户消息与助手消息，并刷新会话。

        ``lock_owned`` 拿行锁重读是这里唯一能区分「并发清空」与「正常续聊」的手段：
        没有锁时读到的状态可能已经过期，迟到的回答会把清空/删除的结果覆盖回去。
        """
        now = datetime.now(UTC)
        started = perf_counter()
        dependencies = [evidence.to_dependency() for evidence in evidences]
        async with async_session_factory() as db, db.begin():
            session = await KbChatSessionCRUD(db).lock_owned(session_id=session_id, user_id=self.user_id)
            if session is None:
                raise ChatSessionNotFoundError("会话不存在")
            self._assert_turn_alive(prior_status=prior_status, current_status=session.status or SESSION_ACTIVE)

            message_crud = KbChatMessageCRUD(db)
            user_row = await message_crud.create(
                data={
                    "session_id": session_id,
                    "role": MESSAGE_USER,
                    "content": question,
                    "citations": [],
                    "knowledge_deps": [],
                    "status": MESSAGE_ACTIVE,
                }
            )
            assistant_row = await message_crud.create(
                data={
                    "session_id": session_id,
                    "role": MESSAGE_ASSISTANT,
                    "content": answer,
                    "citations": list(citations),
                    "knowledge_deps": dependencies,
                    "status": MESSAGE_ACTIVE,
                }
            )

            # 消息数用 COUNT 而不是 +2：并发提问时两个事务各 +2 会把数字加重复
            session.message_count = await message_crud.count_visible(session_id)
            session.last_active_at = now
            # 清空后再提问：会话回到 active（壳一直保留，cleared 只表示「上一轮历史被清掉」）
            session.status = SESSION_ACTIVE
            if (session.title or "") == DEFAULT_SESSION_TITLE:
                session.title = derive_title(question)
            await db.flush()

        meter.assistant_message_id = assistant_row.uuid
        meter.persist_ms = _elapsed_ms(started)
        return user_row.uuid, assistant_row.uuid

    @staticmethod
    def _assert_turn_alive(*, prior_status: str, current_status: str) -> None:
        """迟到判定：已删除一律丢弃；「生成前是 active、现在被清空」也丢弃。

        为什么只在这两处丢弃：``cleared → cleared`` 属于「清空后才提问」的正常续聊，
        把它也判成迟到会让清空过的会话永远无法再对话。
        """
        if current_status == SESSION_DELETED:
            raise ChatTurnDiscardedError("会话已删除，本轮回答已丢弃")
        if prior_status == SESSION_ACTIVE and current_status == SESSION_CLEARED:
            raise ChatTurnDiscardedError("会话在生成期间被清空，本轮回答已丢弃")

    @staticmethod
    def to_record(row: KbChatMessageModel) -> MessageRecord:
        """消息模型 → 领域快照（``message_id`` 由 ``uuid`` 投影）。"""
        return MessageRecord(
            message_id=row.uuid,
            session_id=row.session_id,
            role=row.role,
            content=row.content,
            citations=list(row.citations or []),
            knowledge_deps=list(row.knowledge_deps or []),
            status=row.status,
            created_time=row.created_time,
        )
