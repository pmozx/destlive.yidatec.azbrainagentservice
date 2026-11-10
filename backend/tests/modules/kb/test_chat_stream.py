"""AI 对话流式（/kb/chat/ask/stream）：帧序、落库一致、拒答与超时的错误帧。

验收断言：

1. 帧序：``meta`` 第一、``done`` 最后且唯一、``citations`` 在 ``done`` 之前；
2. 落库一致：``done.answer`` 与库里助手消息正文一致，``quote`` 与切片正文逐字相同，
   消息成对增加；
3. 拒答：一条 ``delta`` + 空 ``citations`` + ``finish_reason=no_evidence``，且**不调模型**；
4. 首帧前拒绝：字段校验 422、身份非法 400、会话不属于该用户 404，且响应体是 JSON 而不是流；
5. 首字超时：``error{TIMEOUT, retryable=true}``，且一条消息都不落库。

数据装置与 `test_chat_admin.py` 共用 `chat_support`：两种出口必须是同一份编排。
"""

from typing import Any

import pytest

from app.config.setting import settings
from app.modules.kb.chat.schema import FINISH_NO_EVIDENCE, FINISH_STOP, NO_EVIDENCE_ANSWER

from tests.modules.kb.chat_support import (
    CONTENT,
    STREAM_PATH,
    body,
    drop_sessions,
    foreign_session,
    hit,
    stub_search,
    stub_stream,
)
from tests.modules.kb.search_support import USER_ID, cleanup, entry, ready, team
from tests.support import call, call_sse


def _events(frames: list[dict[str, Any]]) -> list[str]:
    """帧序列（只取事件名，便于断言顺序）。"""
    return [frame["event"] for frame in frames]


def _first(frames: list[dict[str, Any]], event: str) -> dict[str, Any]:
    """取指定事件的第一个 ``data``（用例里每个事件最多一个，取第一个足够）。"""
    return next(frame["data"] for frame in frames if frame["event"] == event)


async def _message_total(session_id: str) -> int:
    """从「会话记录」页的同一条链路查消息数：与页面看到的口径一致。"""
    detail = await call("GET", f"/api/v1/kb/chat-session/detail/{session_id}")
    assert detail.status_code == 200, detail.text
    return int(detail.json()["data"]["message_total"])


async def test_chat_stream_rejects_blank_question_before_streaming() -> None:
    """空白问题在首帧前就拒绝：响应是 JSON 422，不是一条「开局即错」的流。"""
    response, frames = await call_sse("POST", STREAM_PATH, body=body(question="   "))
    assert response.status_code == 422, response.text
    assert frames == []
    assert "text/event-stream" not in response.headers.get("Content-Type", "")


async def test_chat_stream_rejects_non_numeric_user_id() -> None:
    """``user_id`` 非数字 → 400：算不出授权范围，也不该建出归属不明的会话。"""
    response, frames = await call_sse("POST", STREAM_PATH, body=body(user_id="user-demo"))
    assert response.status_code == 400, response.text
    assert frames == []


async def test_chat_stream_headers_disable_buffering(monkeypatch: pytest.MonkeyPatch) -> None:
    """流式响应头必须能穿透代理（缺 ``X-Accel-Buffering: no`` 就会被攒成一整包）。

    这三个头只加在已经是 200 + ``text/event-stream`` 的响应上，所以走成功流（无证据路径
    最省事），而不是拿首帧前的 400 去断言——那条路径本来就不该有流式头。
    """
    await ready()
    stub_search(monkeypatch, [])
    stub_stream(monkeypatch, [])
    sessions: list[str] = []
    try:
        response, frames = await call_sse("POST", STREAM_PATH, body=body())
        assert response.status_code == 200, response.text
        assert response.headers["Content-Type"].startswith("text/event-stream")
        assert response.headers.get("X-Accel-Buffering") == "no"
        assert "no-transform" in response.headers.get("Cache-Control", "")
        assert response.headers.get("X-Stream-Version") == "1"
        sessions.append(_first(frames, "meta")["session_id"])
    finally:
        await drop_sessions(sessions)


async def test_chat_stream_rejects_foreign_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """会话属于别人：首帧前 404（与「不存在」同一出口），不泄漏他人会话的存在性。"""
    await ready()
    stub_search(monkeypatch, [])
    session_id = await foreign_session()
    try:
        response, frames = await call_sse("POST", STREAM_PATH, body=body(session_id=session_id))
        assert response.status_code == 404, response.text
        assert frames == []
    finally:
        await drop_sessions([session_id])


async def test_chat_stream_orders_frames_and_persists_same_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    """有证据：帧序固定，``done.answer`` 与落库正文、引用逐字一致。"""
    await ready()
    team_id, group_code = await team(members=[USER_ID])
    publication_id, record_id = await entry(
        team=group_code,
        chunks=[{"seq": 1, "heading": "开通流程", "content": CONTENT}],
    )
    deltas = ["按 [1] 在服务台提交申请，", "由负责人开通账号。"]
    stub_search(monkeypatch, [hit(publication_id, record_id)])
    stub_stream(monkeypatch, deltas)
    sessions: list[str] = []
    try:
        response, frames = await call_sse("POST", STREAM_PATH, body=body())
        assert response.status_code == 200, response.text
        assert response.headers["Content-Type"].startswith("text/event-stream")

        events = _events(frames)
        assert events[0] == "meta"
        assert events[-1] == "done"
        assert events.count("done") == 1
        assert "error" not in events
        assert events.index("citations") < events.index("done")
        # 检索与生成两段各一次 stage，且 stage 永远夹在 meta 与 delta 之间
        assert events.count("stage") == 2

        meta = _first(frames, "meta")
        assert meta["v"] == 1
        assert meta["request_id"]
        sessions.append(meta["session_id"])

        deltas_out = [frame["data"] for frame in frames if frame["event"] == "delta"]
        assert [item["seq"] for item in deltas_out] == [1, 2]
        assert [item["text"] for item in deltas_out] == deltas

        citations = _first(frames, "citations")["citations"]
        assert [item["citation_id"] for item in citations] == ["c1"]
        assert citations[0]["quote"] == CONTENT
        assert citations[0]["chunk_id"] == f"{record_id}:1"

        done = _first(frames, "done")
        assert done["answer"] == "".join(deltas)
        assert done["no_evidence"] is False
        assert done["finish_reason"] == FINISH_STOP
        assert done["session_id"] == meta["session_id"]

        # 落库一致：同一会话两条消息，助手正文与 done.answer 一字不差
        assert await _message_total(meta["session_id"]) == 2
        detail = await call("GET", f"/api/v1/kb/chat-message/list?session_id={meta['session_id']}")
        assert detail.status_code == 200, detail.text
        items = detail.json()["data"]["items"]
        assert [item["role"] for item in items] == ["user", "assistant"]
        assert items[1]["content"] == done["answer"]
        assert items[1]["uuid"] == done["assistant_message_id"]
    finally:
        await drop_sessions(sessions)
        await cleanup([publication_id], [team_id])


async def test_chat_stream_no_evidence_emits_single_delta(monkeypatch: pytest.MonkeyPatch) -> None:
    """一条都没召回：固定拒答话术走 delta，citations 为空，不调模型但照样落库成对消息。"""
    await ready()
    stub_search(monkeypatch, [])
    stub_stream(monkeypatch, ["不该被调用"])
    sessions: list[str] = []
    try:
        response, frames = await call_sse("POST", STREAM_PATH, body=body(question="量子退相干时间是多久？"))
        assert response.status_code == 200, response.text
        deltas_out = [frame["data"] for frame in frames if frame["event"] == "delta"]
        assert len(deltas_out) == 1
        assert deltas_out[0]["text"] == NO_EVIDENCE_ANSWER

        assert _first(frames, "citations")["citations"] == []
        done = _first(frames, "done")
        sessions.append(done["session_id"])
        assert done["no_evidence"] is True
        assert done["finish_reason"] == FINISH_NO_EVIDENCE
        assert done["answer"] == NO_EVIDENCE_ANSWER
        assert await _message_total(done["session_id"]) == 2
    finally:
        await drop_sessions(sessions)


async def test_chat_stream_first_token_timeout_emits_error_without_persisting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """首字超时 → ``error{TIMEOUT}``；整轮回滚，不留半截消息。"""
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
        assert _events(frames)[-1] == "error"
        assert "done" not in _events(frames)
        error = _first(frames, "error")
        sessions.append(error["session_id"])
        assert error["code"] == "TIMEOUT"
        assert error["retryable"] is True
        assert await _message_total(error["session_id"]) == 0
    finally:
        await drop_sessions(sessions)
        await cleanup([publication_id], [team_id])
