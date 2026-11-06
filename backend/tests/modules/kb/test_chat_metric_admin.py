"""问答指标管理端接口（``/api/v1/kb/chat-metric``）的回归测试。

验收断言：

1. **明细按会话取**：一行一轮、时间升序，失败轮（没有助手消息）也在列表里；
2. **汇总与列表同口径**：会话 ID / 用户 / 标题 / 状态四个条件作用在会话表上，
   指标行自身的时间范围不参与；
3. **比率与均值在后端算**：空集时拒答率 0、均值为 ``None``，不产生 ``NaN``；
4. **只读**：管理端不提供任何写入口（本文件只用 GET）。

装置与 ``test_chat_metric.py`` 同源：正常轮走真实编排（只打桩召回与模型），
失败轮与拒答轮直接插表——它们本来就只能由异常路径产生，用问答链路造不出来。
"""

import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import delete, update

from app.config.setting import settings
from app.core.database import async_session_factory
from app.core.observability import user_pseudonym
from app.modules.kb.chat.schema import FINISH_NO_EVIDENCE
from app.modules.kb.chat_metric import MODE_SYNC, KbChatTurnMetricModel

from tests.modules.kb.chat_support import ASK_PATH, CONTENT, body, drop_sessions, hit, stub_llm, stub_search
from tests.modules.kb.search_support import TENANT, USER_ID, cleanup, entry, ready, team
from tests.support import call, requires_db

LIST_PATH = "/api/v1/kb/chat-metric/list"
SUMMARY_PATH = "/api/v1/kb/chat-metric/summary"
#: ``DateTimeStr`` 出口是**业务时区**的墙上时间（不是裸 UTC）：页面直接 `new Date()` 才不偏 8 小时
DISPLAY = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")


async def _run_sync_turn(monkeypatch: pytest.MonkeyPatch) -> tuple[str, str, str]:
    """跑一轮正常问答，返回 ``(session_id, publication_id, team_id)``。"""
    await ready()
    team_id, group_code = await team(members=[USER_ID])
    publication_id, record_id = await entry(
        team=group_code,
        chunks=[{"seq": 1, "heading": "开通流程", "content": CONTENT}],
    )
    stub_search(monkeypatch, [hit(publication_id, record_id)])
    stub_llm(monkeypatch, "按 [1] 在服务台提交申请，由负责人开通账号。")
    response = await call("POST", ASK_PATH, body=body())
    assert response.status_code == 200, response.text
    return response.json()["data"]["session_id"], publication_id, team_id


async def _insert_metric(session_id: str, **overrides: Any) -> None:
    """直接插一条指标行（失败轮 / 拒答轮）。"""
    fields: dict[str, Any] = {
        "session_id": session_id,
        "tenant_id": TENANT,
        "user_hash": user_pseudonym(USER_ID),
        "mode": MODE_SYNC,
        "model": "stub-model",
    }
    fields.update(overrides)
    async with async_session_factory() as db, db.begin():
        db.add(KbChatTurnMetricModel(**fields))


async def _pin_total_ms(session_id: str, total_ms: int) -> None:
    """把真实问答轮的耗时钉成确定值：真实耗时不可预测，均值断言得靠它。"""
    async with async_session_factory() as db, db.begin():
        await db.execute(
            update(KbChatTurnMetricModel)
            .where(
                KbChatTurnMetricModel.session_id == session_id,
                KbChatTurnMetricModel.assistant_message_id.isnot(None),
            )
            .values(total_ms=total_ms)
        )


async def _drop_metrics(session_ids: list[str]) -> None:
    """回收指标行：``drop_sessions`` 只清会话与消息，指标表是独立的只增表。"""
    if not session_ids:
        return
    async with async_session_factory() as session, session.begin():
        await session.execute(delete(KbChatTurnMetricModel).where(KbChatTurnMetricModel.session_id.in_(session_ids)))


async def _seed_three_turns(monkeypatch: pytest.MonkeyPatch) -> tuple[str, str, str]:
    """一轮正常 + 一轮失败 + 一轮拒答：均值与比率都能算出确定值。"""
    session_id, publication_id, team_id = await _run_sync_turn(monkeypatch)
    await _pin_total_ms(session_id, 300)
    await _insert_metric(session_id, error_code="TIMEOUT", evidence_count=1, top_score=0.9, total_ms=100, tokens_in=10, tokens_out=5)
    await _insert_metric(
        session_id,
        no_evidence=True,
        finish_reason=FINISH_NO_EVIDENCE,
        total_ms=200,
        tokens_in=20,
        tokens_out=8,
    )
    return session_id, publication_id, team_id


@requires_db
async def test_metric_list_orders_turns_and_keeps_failed_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    """明细：一行一轮、时间升序；失败轮没有助手消息但必须出现在对话流里。"""
    sessions: list[str] = []
    publication_id = team_id = ""
    try:
        session_id, publication_id, team_id = await _seed_three_turns(monkeypatch)
        sessions.append(session_id)

        response = await call("GET", f"{LIST_PATH}?page_no=1&page_size=20&session_id={session_id}")
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["total"] == 3
        items = data["items"]
        assert [item["error_code"] for item in items] == [None, "TIMEOUT", None]

        answered, failed, refused = items
        # 正常轮：三个事实源（消息 / 指标 / 证据）靠 assistant_message_id 串起来
        assert answered["assistant_message_id"]
        assert answered["mode"] == MODE_SYNC
        assert answered["model"] == "stub-model"
        assert answered["evidence_count"] == 1
        assert answered["top_score"] == pytest.approx(0.9)
        assert answered["finish_reason"] == "stop"
        assert answered["total_ms"] == 300
        # 失败轮：只有指标没有助手消息，耗时与证据快照仍在
        assert failed["assistant_message_id"] is None
        assert failed["total_ms"] == 100
        assert failed["evidence_count"] == 1
        # 拒答轮：记 no_evidence，不记 error_code（否则失败率被拒答污染）
        assert refused["no_evidence"] is True
        assert refused["error_code"] is None
        # 时间按业务时区输出：逐字符形状对，且与当前业务时区时间差不超过 5 分钟
        created = answered["created_time"]
        assert DISPLAY.fullmatch(created), created
        local = datetime.strptime(created, "%Y-%m-%d %H:%M:%S").replace(tzinfo=ZoneInfo(settings.TIMEZONE))
        assert abs((datetime.now(ZoneInfo(settings.TIMEZONE)) - local).total_seconds()) < 300, created
    finally:
        await drop_sessions(sessions)
        await _drop_metrics(sessions)
        await cleanup([publication_id], [team_id])


@requires_db
async def test_metric_list_unknown_session_returns_empty_page() -> None:
    """未知会话返回空页而不是 404：指标是否存在与会话是否存在是两件事。"""
    response = await call("GET", f"{LIST_PATH}?page_no=1&page_size=10&session_id=no-such-session")
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert (data["total"], data["items"]) == (0, [])


@requires_db
async def test_metric_list_requires_session_id() -> None:
    """缺 ``session_id`` 直接 422：没有会话维度的「全部指标」不是这个端点的语义。"""
    response = await call("GET", f"{LIST_PATH}?page_no=1&page_size=10")
    assert response.status_code == 422, response.text


@requires_db
async def test_metric_summary_projects_rates_and_means(monkeypatch: pytest.MonkeyPatch) -> None:
    """汇总：轮数 / 拒答率 / 失败轮 / 均值 / 用量合计一次给全，空轮不产生 NaN。"""
    sessions: list[str] = []
    publication_id = team_id = ""
    try:
        session_id, publication_id, team_id = await _seed_three_turns(monkeypatch)
        sessions.append(session_id)

        response = await call("GET", f"{SUMMARY_PATH}?session_id={session_id}")
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["turns"] == 3
        assert data["no_evidence_turns"] == 1
        assert data["no_evidence_rate"] == pytest.approx(1 / 3, abs=1e-4)
        assert data["failed_turns"] == 1
        # (300 + 100 + 200) / 3；首字只有流式轮有值，三行都没有 → None 而不是 0
        assert data["avg_total_ms"] == 200
        assert data["avg_first_token_ms"] is None
        assert (data["tokens_in"], data["tokens_out"]) == (30, 13)
    finally:
        await drop_sessions(sessions)
        await _drop_metrics(sessions)
        await cleanup([publication_id], [team_id])


@requires_db
async def test_metric_summary_filters_follow_session_conditions(monkeypatch: pytest.MonkeyPatch) -> None:
    """汇总的筛选落在会话表上：标题 / 用户 / 状态与会话列表同口径。"""
    sessions: list[str] = []
    publication_id = team_id = ""
    try:
        session_id, publication_id, team_id = await _seed_three_turns(monkeypatch)
        sessions.append(session_id)

        by_title = await call("GET", f"{SUMMARY_PATH}?title=VPN")
        assert by_title.status_code == 200, by_title.text
        assert by_title.json()["data"]["turns"] == 3

        by_user = await call("GET", f"{SUMMARY_PATH}?user_id={USER_ID}")
        assert by_user.status_code == 200, by_user.text
        assert by_user.json()["data"]["turns"] == 3

        # 会话是 active：按已删除筛应是空集，且比率/均值为 0 / None 而不是 NaN
        deleted = await call("GET", f"{SUMMARY_PATH}?status=deleted&session_id={session_id}")
        assert deleted.status_code == 200, deleted.text
        empty = deleted.json()["data"]
        assert (empty["turns"], empty["no_evidence_rate"], empty["avg_total_ms"]) == (0, 0.0, None)

        ghost = await call("GET", f"{SUMMARY_PATH}?session_id={session_id}&user_id={USER_ID + 7777}")
        assert ghost.status_code == 200, ghost.text
        assert ghost.json()["data"]["turns"] == 0
    finally:
        await drop_sessions(sessions)
        await _drop_metrics(sessions)
        await cleanup([publication_id], [team_id])
