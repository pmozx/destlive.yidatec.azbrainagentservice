"""问答测试公共装置：请求体、召回/模型打桩、会话回收。

`test_chat_admin.py`（一次性返回）与 `test_chat_stream.py`（SSE 帧流）验的是**同一轮编排的
两种出口**，装置必须完全一致——否则「流式答对了但非流式答错了」这种回归没人能看出来。

约定：
- 只打桩两个外部依赖（自然语言召回、模型），授权、取证回查、落库、迟到判定全走真实实现；
- 打桩后的数据装置复用 `search_support`（与 #07 契约用例同一套授权链摆法）。
"""

import asyncio
import json
from typing import Any

import pytest
from sqlalchemy import delete

from app.config.setting import settings
from app.core.clients.llm import ChatStreamChunk
from app.core.database import async_session_factory
from app.modules.kb.chat import service as chat_service
from app.modules.kb.chat_message.model import KbChatMessageModel
from app.modules.kb.chat_session.model import KbChatSessionModel
from app.modules.kb.retrieval.schema import SearchHit, SearchResult
from app.modules.kb.retrieval.service import KbRetrievalService

from tests.modules.kb.search_support import KNOWLEDGE_NO, TENANT, TITLE, USER_ID

#: 与 search_support 里那一份切片正文一致：引用要求逐字回查
CONTENT = "在服务台提交 vpn 申请，由负责人开通账号。"
ASK_PATH = "/api/v1/kb/chat/ask"
STREAM_PATH = "/api/v1/kb/chat/ask/stream"
#: 桩模型名：模型名由用途槽位钉选（规范 14.1.2），指标表记的是「本轮实际调用的模型」，
#: 它取自 ModelProfile 而不是 LlmClient.model——因此桩只改配置，不模拟模型名属性
STUB_MODEL = "stub-model"


def body(**overrides: Any) -> bytes:
    """一个合法的问答正文；用例只覆盖自己关心的字段。"""
    payload = {"user_id": str(USER_ID), "tenant_id": TENANT, "question": "如何申请 VPN？", "language": "zh-CN"}
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False).encode()


def hit(publication_id: str, record_id: str) -> SearchHit:
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


def stub_search(monkeypatch: pytest.MonkeyPatch, hits: list[SearchHit]) -> None:
    """自然语言通道打桩：向量化未接入时也能验编排层（召回结果本身由 #07 用例负责）。"""

    async def fake_search(self, data):  # noqa: ANN001, ANN202 - 打桩不需要精确签名
        return SearchResult(hits=tuple(hits), authorized_publication_count=len(hits))

    monkeypatch.setattr(KbRetrievalService, "natural_language_search", fake_search)


def stub_llm(monkeypatch: pytest.MonkeyPatch, answer: str) -> None:
    """一次性生成打桩：只验编排与落库，不把用例绑在真实 LLM 上。"""
    monkeypatch.setattr(settings, "LLM_MODEL_KB_ANSWER", STUB_MODEL)

    class _FakeLlm:
        async def chat(self, messages, **kwargs):  # noqa: ANN001, ANN003, ANN202 - 同上
            return answer

    monkeypatch.setattr(chat_service, "get_llm_client", lambda: _FakeLlm())


def stub_stream(
    monkeypatch: pytest.MonkeyPatch,
    deltas: list[str],
    *,
    delay: float = 0.0,
    error: Exception | None = None,
) -> None:
    """流式生成打桩：``delay`` 制造首字超时，``error`` 制造上游中断。

    ``chat()`` 直接断言失败：流式路径一旦回落到一次性生成，用例必须立刻红。
    """
    monkeypatch.setattr(settings, "LLM_MODEL_KB_ANSWER", STUB_MODEL)

    class _FakeLlm:
        async def chat(self, messages, **kwargs):  # noqa: ANN001, ANN003, ANN202 - 同上
            raise AssertionError("流式路径不应调用一次性生成 chat()")

        async def stream_chat_events(self, messages, **kwargs):  # noqa: ANN001, ANN003, ANN202
            if delay:
                await asyncio.sleep(delay)
            if error is not None:
                raise error
            for text in deltas:
                yield ChatStreamChunk(delta=text)
            # 末片带用量：验证「有值就覆盖、无值就沿用」不会把正文搞乱
            yield ChatStreamChunk(delta="", tokens_in=11, tokens_out=22)

    monkeypatch.setattr(chat_service, "get_llm_client", lambda: _FakeLlm())


async def drop_sessions(session_ids: list[str]) -> None:
    """回收用例产生的会话与消息：消息先删，避免留下查得到的孤儿行。"""
    if not session_ids:
        return
    async with async_session_factory() as session, session.begin():
        await session.execute(delete(KbChatMessageModel).where(KbChatMessageModel.session_id.in_(session_ids)))
        await session.execute(delete(KbChatSessionModel).where(KbChatSessionModel.uuid.in_(session_ids)))


async def foreign_session() -> str:
    """造一条别人的会话：归属隔离只在 user_id 上判定。"""
    async with async_session_factory() as session, session.begin():
        row = KbChatSessionModel(user_id=USER_ID + 1, title="他人的会话", message_count=0)
        session.add(row)
        await session.flush()
        return row.uuid
