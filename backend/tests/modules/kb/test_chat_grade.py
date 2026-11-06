"""灰区二判在问答编排里的接线（``kb/chat/service._insufficient``）。

三条验收：

1. **开启且相关度偏低**：让模型判一次「资料够不够」，判不足就走拒答分支——不再调生成模型，
   ``no_evidence=true`` + ``finish_reason=no_evidence``，两行消息照旧落库；
2. **相关度过线**：一次调用都不多花（判定是灰区增强，不是每轮都跑的固定成本）；
3. **默认关闭**：``KB_GRADE_JUDGE_ENABLE=false`` 时编排行为与接入前完全一致。

装置与 ``test_chat_admin.py`` 共用 ``chat_support``：只打桩召回、模型与二判本身，其余真实实现。
"""

from dataclasses import replace
from typing import Any

import pytest

from app.config.setting import settings
from app.modules.kb.chat import service as chat_service
from app.modules.kb.chat.schema import FINISH_NO_EVIDENCE, FINISH_STOP, NO_EVIDENCE_ANSWER

from tests.modules.kb.chat_support import ASK_PATH, CONTENT, body, drop_sessions, hit, stub_llm, stub_search
from tests.modules.kb.search_support import USER_ID, cleanup, entry, ready, team
from tests.support import call, requires_db

#: 低于 KB_GRADE_JUDGE_TOP_SCORE（0.55）的召回分：灰区
LOW_SCORE = 0.3


async def _recall_at(monkeypatch: pytest.MonkeyPatch, score: float) -> tuple[str, str]:
    """造一份带证据的授权数据，并把自然语言通道的召回分钉在 ``score``。"""
    await ready()
    team_id, group_code = await team(members=[USER_ID])
    publication_id, record_id = await entry(
        team=group_code,
        chunks=[{"seq": 1, "heading": "开通流程", "content": CONTENT}],
    )
    stub_search(monkeypatch, [replace(hit(publication_id, record_id), score=score)])
    return team_id, publication_id


def _spy_judge(monkeypatch: pytest.MonkeyPatch, verdict: tuple[bool, str]) -> list[str]:
    """把二判换成记录调用的桩（``verdict`` 决定结论）。"""
    calls: list[str] = []

    async def fake_judge(question: str, recall: str) -> tuple[bool, str]:
        calls.append(question)
        return verdict

    monkeypatch.setattr(chat_service, "judge_sufficiency", fake_judge)
    return calls


def _forbid_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    """生成模型一旦被调用就让用例立刻红。"""

    class _ForbiddenLlm:
        model = "stub-model"

        async def chat(self, messages: Any, **kwargs: Any) -> str:  # noqa: ANN401 - 打桩
            raise AssertionError("判为资料不足时不应调用生成模型")

    monkeypatch.setattr(chat_service, "get_llm_client", lambda: _ForbiddenLlm())


@requires_db
async def test_judge_rejection_turns_into_no_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    """开启二判 + 相关度偏低 + 模型判「不足」→ 固定拒答话术，不调生成模型。"""
    monkeypatch.setattr(settings, "KB_GRADE_JUDGE_ENABLE", True)
    team_id, publication_id = await _recall_at(monkeypatch, LOW_SCORE)
    calls = _spy_judge(monkeypatch, (False, "缺少审批人"))
    _forbid_generation(monkeypatch)
    sessions: list[str] = []
    try:
        response = await call("POST", ASK_PATH, body=body())
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        sessions.append(data["session_id"])

        assert len(calls) == 1, "灰区应恰好判一次"
        assert data["no_evidence"] is True
        assert data["finish_reason"] == FINISH_NO_EVIDENCE
        assert data["answer"] == NO_EVIDENCE_ANSWER
        assert data["citations"] == []
    finally:
        await drop_sessions(sessions)
        await cleanup([publication_id], [team_id])


@requires_db
async def test_judge_skipped_when_score_is_high(monkeypatch: pytest.MonkeyPatch) -> None:
    """相关度过线：不做二判，直接按原链路回答（不多花一次模型调用）。"""
    monkeypatch.setattr(settings, "KB_GRADE_JUDGE_ENABLE", True)
    team_id, publication_id = await _recall_at(monkeypatch, 0.9)
    # 桩若被调用就会判「不足」→ 用例失败，因此既能验「没调用」，也能验「没走拒答」
    calls = _spy_judge(monkeypatch, (False, "不该被问"))
    stub_llm(monkeypatch, "按 [1] 在服务台提交申请，由负责人开通账号。")
    sessions: list[str] = []
    try:
        response = await call("POST", ASK_PATH, body=body())
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        sessions.append(data["session_id"])

        assert calls == []
        assert data["no_evidence"] is False
        assert data["finish_reason"] == FINISH_STOP
    finally:
        await drop_sessions(sessions)
        await cleanup([publication_id], [team_id])


@requires_db
async def test_judge_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认关闭：低分证据照常回答，编排行为与接入前一致。"""
    monkeypatch.setattr(settings, "KB_GRADE_JUDGE_ENABLE", False)
    team_id, publication_id = await _recall_at(monkeypatch, LOW_SCORE)
    calls = _spy_judge(monkeypatch, (False, "不该被问"))
    stub_llm(monkeypatch, "按 [1] 在服务台提交申请。")
    sessions: list[str] = []
    try:
        response = await call("POST", ASK_PATH, body=body())
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        sessions.append(data["session_id"])

        assert calls == []
        assert data["no_evidence"] is False
    finally:
        await drop_sessions(sessions)
        await cleanup([publication_id], [team_id])
