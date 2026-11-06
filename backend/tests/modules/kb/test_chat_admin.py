"""AI 对话管理端（/kb/chat/ask）：试问台的一次问答 + 归属/依赖口径。

验收断言：

1. 一次问答真正落库：自动建会话、写两轮消息，引用 ``quote`` 与切片正文逐字一致；
2. 同一会话再问一轮落在同一会话上，消息数累加（不是新建会话）；
3. 没召回任何切片时走固定拒答话术 + ``finish_reason=no_evidence``，不调模型；
4. 会话不属于该用户 → 404；``user_id`` 非数字 → 400（不到检索链路）。

数据装置复用 ``search_support``（与 #07 契约用例同一套授权链摆法）：只把自然语言通道的
召回结果与模型回答打桩，其余（授权、取证回查、落库、迟到判定）都走真实实现。
"""

import json
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import delete

from app.core.database import async_session_factory
from app.modules.kb.chat import service as chat_service
from app.modules.kb.chat.schema import (
    FINISH_NO_EVIDENCE,
    FINISH_STOP,
    NO_EVIDENCE_ANSWER,
    KbChatAskSchema,
)
from app.modules.kb.chat_message.model import KbChatMessageModel
from app.modules.kb.chat_session.model import KbChatSessionModel
from app.modules.kb.retrieval.schema import SearchHit, SearchResult
from app.modules.kb.retrieval.service import KbRetrievalService

from tests.modules.kb.search_support import (
    KNOWLEDGE_NO,
    TENANT,
    TITLE,
    USER_ID,
    cleanup,
    entry,
    ready,
    team,
)
from tests.support import call, requires_db

CONTENT = "在服务台提交 vpn 申请，由负责人开通账号。"
ASK_PATH = "/api/v1/kb/chat/ask"


def _body(**overrides: Any) -> bytes:
    payload = {"user_id": str(USER_ID), "tenant_id": TENANT, "question": "如何申请 VPN？", "language": "zh-CN"}
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False).encode()


def _hit(publication_id: str, record_id: str) -> SearchHit:
    """一条 chunk 级命中（字段口径与 #07 一致，``snippet`` 是切片完整正文）。"""
    return SearchHit(
        resource_type="document",
        resource_id=f"doc-{publication_id}",
        publication_id=publication_id,
        record_id=record_id,
        version_id="1",
        revision=101,
        knowledge_no=KNOWLEDGE_NO,
        chunk_id=f"{record_id}:1",
        title=TITLE,
        snippet=CONTENT,
        score=0.9,
        page_from=1,
        page_to=1,
    )


def _stub_search(monkeypatch: pytest.MonkeyPatch, hits: list[SearchHit]) -> None:
    """自然语言通道打桩：向量化未接入时也能验编排层（召回结果本身就由 #07 用例负责）。"""

    async def fake_search(self, data):  # noqa: ANN001, ANN202 - 打桩不需要精确签名
        return SearchResult(hits=tuple(hits), authorized_publication_count=len(hits))

    monkeypatch.setattr(KbRetrievalService, "natural_language_search", fake_search)


def _stub_llm(monkeypatch: pytest.MonkeyPatch, answer: str) -> None:
    """模型打桩：只验编排与落库，不把用例绑在真实 LLM 上。"""

    class _FakeLlm:
        # 编排层把模型名记进指标表：桩对象必须具备与 LlmClient 相同的接口面
        model = "stub-model"

        async def chat(self, messages, **kwargs):  # noqa: ANN001, ANN003, ANN202 - 同上
            return answer

    monkeypatch.setattr(chat_service, "get_llm_client", lambda: _FakeLlm())


async def _drop_sessions(session_ids: list[str]) -> None:
    """回收用例产生的会话与消息：消息先删，避免留下查得到的孤儿行。"""
    if not session_ids:
        return
    async with async_session_factory() as session, session.begin():
        await session.execute(delete(KbChatMessageModel).where(KbChatMessageModel.session_id.in_(session_ids)))
        await session.execute(delete(KbChatSessionModel).where(KbChatSessionModel.uuid.in_(session_ids)))


async def _foreign_session() -> str:
    """造一条别人的会话：归属隔离只在 user_id 上判定。"""
    async with async_session_factory() as session, session.begin():
        row = KbChatSessionModel(user_id=USER_ID + 1, title="他人的会话", message_count=0)
        session.add(row)
        await session.flush()
        return row.uuid


def test_chat_ask_schema_rejects_blank_question() -> None:
    """纯空白问题在入参层就被挡住：没有问题时检索与生成都没有意义。"""
    with pytest.raises(ValidationError):
        KbChatAskSchema(user_id="1", question="   ")


async def test_chat_ask_rejects_non_numeric_user_id() -> None:
    """``user_id`` 非数字 → 400：算不出授权范围，也不该建出归属不明的会话。"""
    response = await call("POST", ASK_PATH, body=_body(user_id="user-demo"))
    assert response.status_code == 400, response.text
    assert "数字" in response.json()["msg"]


@requires_db
async def test_chat_ask_answers_with_citations(monkeypatch: pytest.MonkeyPatch) -> None:
    """有证据：回答落库、引用逐字回查、同一会话续聊不新建会话。"""
    await ready()
    team_id, group_code = await team(members=[USER_ID])
    publication_id, record_id = await entry(
        team=group_code,
        chunks=[{"seq": 1, "heading": "开通流程", "content": CONTENT}],
    )
    _stub_search(monkeypatch, [_hit(publication_id, record_id)])
    _stub_llm(monkeypatch, "按 [1] 在服务台提交申请，由负责人开通账号。")
    sessions: list[str] = []
    try:
        response = await call("POST", ASK_PATH, body=_body())
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        sessions.append(data["session_id"])

        assert data["no_evidence"] is False
        assert data["finish_reason"] == FINISH_STOP
        assert [item["citation_id"] for item in data["citations"]] == ["c1"]
        citation = data["citations"][0]
        assert citation["publication_id"] == publication_id
        assert citation["record_id"] == record_id
        assert citation["chunk_id"] == f"{record_id}:1"
        # 引用必须能在切片正文里逐字找到（按命中键回查库内原文，不直接用检索结果）
        assert citation["quote"] == CONTENT
        assert data["elapsed_ms"] >= 0

        detail = await call("GET", f"/api/v1/kb/chat-session/detail/{data['session_id']}")
        assert detail.status_code == 200, detail.text
        session_data = detail.json()["data"]
        assert session_data["user_id"] == USER_ID
        assert session_data["title"] == "如何申请 VPN？"
        assert session_data["message_total"] == 2

        second = await call("POST", ASK_PATH, body=_body(session_id=data["session_id"], question="办理需要多久？"))
        assert second.status_code == 200, second.text
        assert second.json()["data"]["session_id"] == data["session_id"]
        detail = await call("GET", f"/api/v1/kb/chat-session/detail/{data['session_id']}")
        assert detail.json()["data"]["message_total"] == 4
    finally:
        await _drop_sessions(sessions)
        await cleanup([publication_id], [team_id])


@requires_db
async def test_chat_ask_returns_no_evidence_without_recall(monkeypatch: pytest.MonkeyPatch) -> None:
    """一条都没召回：走固定拒答话术，不调模型、不给 citations。"""
    await ready()
    _stub_search(monkeypatch, [])
    _stub_llm(monkeypatch, "不该被调用")
    sessions: list[str] = []
    try:
        response = await call("POST", ASK_PATH, body=_body(question="量子计算的退相干时间是多久？"))
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        sessions.append(data["session_id"])
        assert data["no_evidence"] is True
        assert data["finish_reason"] == FINISH_NO_EVIDENCE
        assert data["answer"] == NO_EVIDENCE_ANSWER
        assert data["citations"] == []
    finally:
        await _drop_sessions(sessions)


@requires_db
async def test_chat_session_list_exposes_session_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """列表页的「会话 ID」列必须有值：契约字段 ``session_id`` 是列 ``uuid`` 的投影。

    通用分页是 ``out_schema.model_validate(orm_row)``，ORM 行没有 ``session_id`` 属性，
    不显式投影就会静默落成空串——前端整列空白，而详情接口同名字段却有值。
    """
    await ready()
    _stub_search(monkeypatch, [])
    sessions: list[str] = []
    try:
        response = await call("POST", ASK_PATH, body=_body())
        assert response.status_code == 200, response.text
        session_id = response.json()["data"]["session_id"]
        sessions.append(session_id)

        listed = await call("GET", f"/api/v1/kb/chat-session/list?page_no=1&page_size=10&user_id={USER_ID}")
        assert listed.status_code == 200, listed.text
        rows = [item for item in listed.json()["data"]["items"] if item["uuid"] == session_id]
        assert rows, f"列表里找不到刚建的会话：{listed.text}"
        assert rows[0]["session_id"] == session_id

        # 「会话 ID」筛选也必须真的落到 ``uuid`` 列上：字段名与列名不映射会被
        # 通用 CRUD 的 ``hasattr`` 兜底静默跳过，于是筛选失效、返回全量。
        filtered = await call(
            "GET", f"/api/v1/kb/chat-session/list?page_no=1&page_size=10&session_id={session_id}"
        )
        assert filtered.status_code == 200, filtered.text
        filtered_data = filtered.json()["data"]
        assert filtered_data["total"] == 1, filtered.text
        assert filtered_data["items"][0]["uuid"] == session_id
    finally:
        await _drop_sessions(sessions)


@requires_db
async def test_chat_ask_rejects_foreign_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """会话属于别人：与「不存在」同一个出口（404），不泄漏他人会话的存在性。"""
    await ready()
    _stub_search(monkeypatch, [])
    session_id = await _foreign_session()
    try:
        response = await call("POST", ASK_PATH, body=_body(session_id=session_id))
        assert response.status_code == 404, response.text
    finally:
        await _drop_sessions([session_id])
