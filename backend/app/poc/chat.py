"""#14 生成回答：检索 → 拼上下文 → LLM 生成 → citations / source_snapshots。

口径：
1. **无证据直接拒答**：检索无命中就返回 `no_evidence=true` + `finish_reason=no_evidence`，不调模型 ——
   契约明文要求「不能使用模型常识编造企业规则」；
2. `prompt_text` 由 GSI 下发，原样作为 system 提示词；AI 只在其后追加一节「引用格式要求」，
   参考资料与问题放在 user 消息里，不覆盖 GSI 的业务约束；
3. `quote` 必须是切片正文的**逐字子串**：模型给的 `[n]` 只决定「引用哪条切片」，具体引文由 AI
   从该切片正文里挑与答案句最相似的一句 —— 模型复述的句子会改字，GSI 拿去做高亮必然定位失败；
4. `citation_id` 由 AI 生成（c1..cn）：契约没规定它的生成方，GSI 请求也不下传 contexts，
   已登记 T0.4 疑点；
5. POC 不做多轮历史回灌（T5.3）、不做权限过滤（T4.3）；`session_id` 传空时由 AI 生成。
"""

import re
import uuid
from typing import Any, Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.config.setting import settings
from app.core.clients.llm import ChatMessage, LlmClient
from app.core.logger import logger

from . import store
from .metrics import StageTimer
from .retrieval import retrieve_for_answer
from .schemas import ChatCompletionRequest, request_digest

# 无证据拒答话术：与 integration_chat 的 NOT_FOUND_MESSAGE 同义，POC 独立定义避免跨模块耦合
NO_EVIDENCE_ANSWER: Final[str] = "知识库中未找到与该问题相关的内容，请补充相关文档或换个问法。"
# 引文上限：够高亮定位即可，不把整段正文塞进响应
QUOTE_MAX_CHARS: Final[int] = 200
# 追加到 GSI 提示词后的引用格式要求（改这里要同步更新 T0.4 冒烟记录）
CITATION_RULES: Final[str] = """【引用要求】
1. 只能使用【参考资料】作答，不得用你自身掌握的知识补充、推测或联想。
2. 每条结论句后用 [n] 标注来源，n 为【参考资料】中的编号；同一句可标多个，如 [1][3]。
3. 资料未覆盖时直接说明「知识库中未找到与该问题相关的内容」，不要编造。"""

_CITATION_MARK = re.compile(r"\[(\d{1,2})\]")
_SENTENCE_SPLIT = re.compile(r"(?<=[。！？；!?;])\s*")
_WHITESPACE = re.compile(r"\s+")
_SNAPSHOT_FIELDS: Final[tuple[str, ...]] = (
    "resource_type",
    "resource_id",
    "publication_id",
    "record_id",
    "version_id",
    "revision",
)


def _bigrams(text: str) -> set[str]:
    """字符二元组集合：中文没有空格，用二元组重合度当「句子相似」的近似。"""
    compact = _WHITESPACE.sub("", text or "")
    if len(compact) < 2:
        return {compact} if compact else set()
    return {compact[index : index + 2] for index in range(len(compact) - 1)}


def pick_quote(answer_sentence: str, content: str, *, limit: int = QUOTE_MAX_CHARS) -> str:
    """在切片正文里挑与答案句最相似的一句作为引文（结果一定是正文的逐字子串）。"""
    sentences = [piece.strip() for piece in _SENTENCE_SPLIT.split(content or "") if piece.strip()]
    if not sentences:
        return (content or "").strip()[:limit]
    target = _bigrams(answer_sentence)

    def overlap(sentence: str) -> float:
        grams = _bigrams(sentence)
        return len(grams & target) / max(1, len(grams))

    return max(sentences, key=overlap)[:limit]


def build_citations(answer: str, hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按答案里的 `[n]` 生成 citation 列表；编号越界只告警不编造。"""
    marked: dict[int, str] = {}
    for sentence in _SENTENCE_SPLIT.split(answer or ""):
        for mark in _CITATION_MARK.findall(sentence):
            marked.setdefault(int(mark), sentence)

    citations: list[dict[str, Any]] = []
    for index in sorted(marked):
        if not 1 <= index <= len(hits):
            logger.warning("POC #14 回答引用了不存在的资料编号 [{}]（共 {} 条），已忽略", index, len(hits))
            continue
        hit = hits[index - 1]
        citation = {"index": index, "citation_id": f"c{index}"}
        citation.update({field: hit[field] for field in (*_SNAPSHOT_FIELDS, "chunk_id") if hit.get(field) is not None})
        citation["quote"] = pick_quote(marked[index], str(hit.get("content") or ""))
        citations.append(citation)

    if hits and not citations:
        logger.warning("POC #14 回答未标注任何来源编号，citations 返回空数组（提示词遵循度问题，非契约错误）")
    return citations


def build_source_snapshots(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按快照去重（同一 slice 集合的多条引用只回一份快照）。"""
    seen: set[tuple[Any, ...]] = set()
    snapshots: list[dict[str, Any]] = []
    for row in rows:
        key = tuple(row.get(field) for field in _SNAPSHOT_FIELDS)
        if key in seen:
            continue
        seen.add(key)
        snapshots.append({field: row[field] for field in _SNAPSHOT_FIELDS if row.get(field) is not None})
    return snapshots


def _render_reference(index: int, hit: dict[str, Any]) -> str:
    """单条参考资料：编号 + 标题/编号/页码 + 正文（正文按剩余预算截断，保留引用所需内容）。"""
    meta = [f"[{index}] 《{hit.get('title') or '未命名'}》", f"知识编号 {hit.get('knowledge_no') or '-'}"]
    if hit.get("page_from"):
        meta.append(f"页码 {hit['page_from']}")
    content = str(hit.get("content") or "").strip()
    budget = max(200, settings.KB_CONTEXT_MAX_CHARS // 4)
    if len(content) > budget:
        content = content[:budget] + "……"
    return "｜".join(meta) + "\n" + content


def build_context(hits: list[dict[str, Any]]) -> str:
    """渲染参考资料总块：编号必须与答案里的 `[n]` 一一对应，所以超预算就整块丢弃而不是截断编号。"""
    blocks: list[str] = []
    used = 0
    for index, hit in enumerate(hits, start=1):
        block = _render_reference(index, hit)
        if blocks and used + len(block) > settings.KB_CONTEXT_MAX_CHARS:
            break
        blocks.append(block)
        used += len(block)
    return "\n\n".join(blocks)


def build_messages(payload: ChatCompletionRequest, hits: list[dict[str, Any]]) -> list[ChatMessage]:
    """system = GSI 下发的提示词 + 引用要求；user = 参考资料 + 问题。

    把「共几条资料」写在资料旁边是有意为之：只靠 system 里一句「编号只能用参考资料中的」时，
    模型会凭印象编出超出范围的编号（T0.3 实测出现过 [9]），把上限摆在正文末尾更管用。
    """
    note = f"（以上共 {len(hits)} 条资料，引用编号只能取 1–{len(hits)}）"
    return [
        {"role": "system", "content": f"{payload.prompt_text.strip()}\n\n{CITATION_RULES}"},
        {"role": "user", "content": f"【参考资料】\n{build_context(hits)}\n{note}\n\n【当前问题】\n{payload.question}"},
    ]


async def answer(
    db: AsyncSession,
    payload: ChatCompletionRequest,
    *,
    timer: StageTimer | None = None,
) -> dict[str, Any]:
    """生成一次回答；同 `request_id` 同正文重试直接返回首次回执（幂等）。"""
    timer = timer or StageTimer()
    digest = request_digest(payload)
    existing = await store.ensure_idempotent(db, scope="chat", key=payload.request_id, body_hash=digest)
    if existing is not None:
        logger.info("POC #14 幂等重试，返回首次回执：request_id={}", payload.request_id)
        return existing

    session_id = payload.session_id.strip() or f"ai-session-{uuid.uuid4().hex[:16]}"
    user_message_id = f"ai-user-{uuid.uuid4().hex[:16]}"
    assistant_message_id = f"ai-assistant-{uuid.uuid4().hex[:16]}"

    hits = await retrieve_for_answer(db, question=payload.question, timer=timer)
    if not hits:
        answer_text = NO_EVIDENCE_ANSWER
        citations: list[dict[str, Any]] = []
        snapshots: list[dict[str, Any]] = []
        no_evidence = True
        finish_reason = "no_evidence"
        logger.info("POC #14 无命中，直接拒答：request_id={} question={}", payload.request_id, payload.question)
    else:
        logger.debug("POC #14 忽略 model_key={}：POC 统一走 LLM_MODEL 配置", payload.model_key)
        async with timer.stage("prompt"):
            messages = build_messages(payload, hits)
        async with timer.stage("llm"):
            answer_text = await LlmClient().chat(
                messages,
                temperature=0.2,
                max_tokens=payload.options.max_output_tokens,
            )
        citations = build_citations(answer_text, hits)
        # 被引用的切片才算「实际使用的来源」；模型没标注时回落到进入上下文的全部切片
        snapshots = build_source_snapshots(citations or hits)
        no_evidence = False
        finish_reason = "stop"

    response: dict[str, Any] = {
        "request_id": payload.request_id,
        "session_id": session_id,
        "user_message_id": user_message_id,
        "assistant_message_id": assistant_message_id,
        "status": "completed",
        "answer": answer_text,
        "citations": citations,
        "source_snapshots": snapshots,
        "authorization_scope_id": payload.authorization.scope_id,
        "no_evidence": no_evidence,
        "finish_reason": finish_reason,
    }

    await store.insert_chat_message(
        db,
        session_id=session_id,
        message_id=user_message_id,
        user_id=payload.user_id,
        role="user",
        content=payload.question,
        request_id=payload.request_id,
    )
    await store.insert_chat_message(
        db,
        session_id=session_id,
        message_id=assistant_message_id,
        user_id=payload.user_id,
        role="assistant",
        content=answer_text,
        request_id=payload.request_id,
    )
    await store.put_idempotency(db, scope="chat", key=payload.request_id, body_hash=digest, response=response)
    await store.record_metric(db, kind="chat", key=payload.request_id, total_ms=timer.total_ms, stages=timer.snapshot())
    logger.info(
        "POC #14 完成：request_id={} hits={} citations={} no_evidence={} 耗时={}ms",
        payload.request_id,
        len(hits),
        len(citations),
        no_evidence,
        timer.total_ms,
    )
    return response
