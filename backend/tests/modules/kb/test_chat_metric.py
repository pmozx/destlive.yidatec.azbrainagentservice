"""AI 问答单轮指标（``sbp_ai_kb_chat_turn_metric``）的回归测试。

验收断言：

1. **一轮一行**：非流式与流式各写一条，``assistant_message_id`` 能对回落库的助手消息
   （指标与消息是两个事实源，必须靠它串起来）；
2. **质量口径**：``evidence_count`` / ``top_score`` 记的是真正进入上下文的那几条证据；
3. **成本口径**：流式取末片 usage（``tokens_in/out``）与首字耗时；非流式门面不返回用量，记 0；
4. **拒答与失败分开记**：拒答是正常轮（``finish_reason=no_evidence``），失败轮看 ``error_code``；
5. **写不进去也不能毁掉问答**：指标落库异常只在内部告警。

装置与 ``test_chat_admin.py`` / ``test_chat_stream.py`` 共用 ``chat_support``：只打桩召回与模型，
授权、取证回查、落库、指标写入全走真实实现。
"""

from typing import Any

import pytest
from sqlalchemy import delete, select

from app.config.setting import settings
from app.core.database import async_session_factory
from app.core.observability import user_pseudonym
from app.modules.kb.chat.schema import FINISH_NO_EVIDENCE, FINISH_STOP
from app.modules.kb.chat_metric import MODE_STREAM, MODE_SYNC, KbChatTurnMetricModel, TurnMeter
from app.modules.kb.chat_metric import meter as meter_module

from tests.modules.kb.chat_support import (
    ASK_PATH,
    CONTENT,
    STREAM_PATH,
    body,
    drop_sessions,
    hit,
    stub_llm,
    stub_search,
    stub_stream,
)
from tests.modules.kb.search_support import TENANT, USER_ID, cleanup, entry, ready, team
from tests.support import call, call_sse, requires_db


async def _metrics(session_id: str) -> list[KbChatTurnMetricModel]:
    """取某会话的全部指标行（本轮只应有一条）。"""
    async with async_session_factory() as db:
        result = await db.execute(
            select(KbChatTurnMetricModel).where(KbChatTurnMetricModel.session_id == session_id)
        )
        return list(result.scalars().all())


async def _drop_metrics(session_ids: list[str]) -> None:
    """回收指标行：``drop_sessions`` 只清会话与消息，指标表是独立的只增表。"""
    if not session_ids:
        return
    async with async_session_factory() as session, session.begin():
        await session.execute(delete(KbChatTurnMetricModel).where(KbChatTurnMetricModel.session_id.in_(session_ids)))


def _first(frames: list[dict[str, Any]], event: str) -> dict[str, Any]:
    """取指定事件的第一个 ``data``（每个事件在本轮最多一个）。"""
    return next(frame["data"] for frame in frames if frame["event"] == event)


def _meter(**overrides: Any) -> TurnMeter:
    """一条字段齐全的度量累加器（不落库）。"""
    fields: dict[str, Any] = {"session_id": "session-1", "tenant_id": "tenant-1", "user_id": 1, "mode": MODE_SYNC}
    fields.update(overrides)
    return TurnMeter(**fields)


def test_meter_takes_evidence_count_and_top_score() -> None:
    """证据口径取「真正进入上下文的条数」与最高分；没有证据时两者分别为 0 / None。"""
    meter = _meter()
    meter.evidences([0.2, 0.9, 0.5])
    assert (meter.evidence_count, meter.top_score) == (3, 0.9)

    empty = _meter()
    empty.evidences([])
    assert (empty.evidence_count, empty.top_score) == (0, None)


def test_meter_prompt_hash_ignores_surrounding_blank() -> None:
    """提示词指纹只取前 16 位，且先归一空白——编辑器的尾随空格不该算成换了提示词。"""
    plain, padded = _meter(), _meter()
    plain.prompt("你是服务台的问答助手。")
    padded.prompt("  你是服务台的问答助手。\n")

    assert plain.prompt_hash is not None
    assert len(plain.prompt_hash) == 16
    assert plain.prompt_hash == padded.prompt_hash


async def test_meter_swallows_write_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """指标写不进去只告警：观测数据不能把一次正常问答变成错误帧。"""

    def _broken_factory():  # noqa: ANN202 - 打桩不需要精确签名
        raise RuntimeError("db down")

    monkeypatch.setattr(meter_module, "async_session_factory", _broken_factory)

    await _meter().save(finish_reason=FINISH_STOP)  # 不抛即通过
    await _meter().fail("INTERNAL")


@requires_db
async def test_sync_turn_records_metric(monkeypatch: pytest.MonkeyPatch) -> None:
    """非流式：一行指标能对回助手消息，质量与耗时口径齐全，用量记 0。"""
    await ready()
    team_id, group_code = await team(members=[USER_ID])
    publication_id, record_id = await entry(
        team=group_code,
        chunks=[{"seq": 1, "heading": "开通流程", "content": CONTENT}],
    )
    stub_search(monkeypatch, [hit(publication_id, record_id)])
    stub_llm(monkeypatch, "按 [1] 在服务台提交申请，由负责人开通账号。")
    sessions: list[str] = []
    try:
        response = await call("POST", ASK_PATH, body=body())
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        sessions.append(data["session_id"])

        rows = await _metrics(data["session_id"])
        assert len(rows) == 1, "一轮问答只写一条指标"
        row = rows[0]
        assert row.mode == MODE_SYNC
        assert row.model == "stub-model"
        assert row.tenant_id == TENANT
        # 用户标识只存伪名：外部看到的是哈希，不是账号原值
        assert row.user_hash == user_pseudonym(USER_ID)
        assert row.evidence_count == 1
        assert row.top_score == pytest.approx(0.9)
        assert row.assistant_message_id == data["assistant_message_id"]
        assert row.no_evidence is False
        assert row.finish_reason == FINISH_STOP
        assert row.error_code is None
        assert row.invalid_citations == 0
        # 非流式门面不返回用量：记 0 而不是猜
        assert (row.tokens_in, row.tokens_out) == (0, 0)
        assert row.first_token_ms is None
        assert row.retrieve_ms >= 0 and row.generate_ms >= 0 and row.persist_ms >= 0
        assert row.total_ms >= row.retrieve_ms + row.generate_ms
        assert row.prompt_hash is not None and len(row.prompt_hash) == 16
        assert row.trace_id is None, "测试环境未开追踪：trace_id 必须为空而不是随机串"
    finally:
        await drop_sessions(sessions)
        await _drop_metrics(sessions)
        await cleanup([publication_id], [team_id])


@requires_db
async def test_stream_turn_records_usage_and_first_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """流式：末片 usage 与首字耗时都要落到指标上（这是成本与体验的唯一数据源）。"""
    await ready()
    team_id, group_code = await team(members=[USER_ID])
    publication_id, record_id = await entry(
        team=group_code,
        chunks=[{"seq": 1, "heading": "开通流程", "content": CONTENT}],
    )
    stub_search(monkeypatch, [hit(publication_id, record_id)])
    stub_stream(monkeypatch, ["按 [1] 在服务台提交申请，", "由负责人开通账号。"])
    sessions: list[str] = []
    try:
        response, frames = await call_sse("POST", STREAM_PATH, body=body())
        assert response.status_code == 200, response.text
        done = _first(frames, "done")
        sessions.append(done["session_id"])

        rows = await _metrics(done["session_id"])
        assert len(rows) == 1
        row = rows[0]
        assert row.mode == MODE_STREAM
        assert row.evidence_count == 1
        assert row.assistant_message_id == done["assistant_message_id"]
        assert row.finish_reason == FINISH_STOP
        assert (row.tokens_in, row.tokens_out) == (11, 22)
        assert row.first_token_ms is not None and row.first_token_ms >= 0
        assert row.generate_ms >= row.first_token_ms
    finally:
        await drop_sessions(sessions)
        await _drop_metrics(sessions)
        await cleanup([publication_id], [team_id])


@requires_db
async def test_no_evidence_turn_is_not_counted_as_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """拒答是正常轮：记 ``no_evidence`` 而不是 ``error_code``，否则失败率会被拒答污染。"""
    await ready()
    stub_search(monkeypatch, [])
    stub_llm(monkeypatch, "不该被调用")
    sessions: list[str] = []
    try:
        response = await call("POST", ASK_PATH, body=body(question="量子退相干时间是多久？"))
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        sessions.append(data["session_id"])
        assert data["no_evidence"] is True

        rows = await _metrics(data["session_id"])
        assert len(rows) == 1
        row = rows[0]
        assert row.no_evidence is True
        assert row.finish_reason == FINISH_NO_EVIDENCE
        assert row.error_code is None
        assert (row.evidence_count, row.top_score) == (0, None)
        assert row.generate_ms == 0, "没调模型就不该有生成耗时"
        assert row.tokens_out == 0
    finally:
        await drop_sessions(sessions)
        await _drop_metrics(sessions)


@requires_db
async def test_failed_turn_records_error_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """首字超时：指标留一条失败轮（有失败码、无助手消息），这样「失败率」才查得出来。"""
    await ready()
    team_id, group_code = await team(members=[USER_ID])
    publication_id, record_id = await entry(
        team=group_code,
        chunks=[{"seq": 1, "heading": "开通流程", "content": CONTENT}],
    )
    monkeypatch.setattr(settings, "KB_LLM_FIRST_TOKEN_TIMEOUT", 0.05)
    stub_search(monkeypatch, [hit(publication_id, record_id)])
    stub_stream(monkeypatch, ["太慢了"], delay=1.0)
    sessions: list[str] = []
    try:
        response, frames = await call_sse("POST", STREAM_PATH, body=body())
        assert response.status_code == 200, response.text
        error = _first(frames, "error")
        sessions.append(error["session_id"])
        assert error["code"] == "TIMEOUT"

        rows = await _metrics(error["session_id"])
        assert len(rows) == 1
        row = rows[0]
        assert row.error_code == "TIMEOUT"
        assert row.assistant_message_id is None
        assert row.finish_reason is None
        assert row.no_evidence is False
        assert row.evidence_count == 1, "失败轮也要留下「当时检索到了什么」"
    finally:
        await drop_sessions(sessions)
        await _drop_metrics(sessions)
        await cleanup([publication_id], [team_id])
