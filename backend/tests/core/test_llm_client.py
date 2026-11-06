"""聊天模型客户端（``app.core.clients.llm``）的抽象层回归测试。

守住三条：① 标准消息按顺序原样送进模型（本层不做二次包装）；
② 末帧 usage 映射到 ``ChatStreamChunk``（落库 token 统计依赖它）；
③ 未配置 / 空回答仍是可读异常，且 503 不被兜底成 502；
④ 调用方给的 ``config`` 原样透传到模型调用（不透传时模型 run 会成为 trace 里的孤立根节点）。
不触达真实模型服务：桩替换底层 LangChain ChatModel。
"""

from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import status
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage, SystemMessage

from app.core.clients.llm import ChatStreamChunk, LlmClient
from app.core.exceptions import CustomException

MODEL = "stub-model"


class _Stub:
    """桩模型：记录收到的消息；``answer`` 走一次性返回，``deltas`` 走流式。"""

    def __init__(self, answer: str = "  答案  ", deltas: tuple[str, ...] = ()) -> None:
        self.answer = answer
        self.deltas = deltas
        self.seen: list[list[BaseMessage]] = []
        self.calls: list[dict[str, Any]] = []

    async def ainvoke(self, messages: list[BaseMessage], **kwargs: Any) -> AIMessage:
        self.seen.append(list(messages))
        self.calls.append(dict(kwargs))
        return AIMessage(content=self.answer)

    async def astream(self, messages: list[BaseMessage], **kwargs: Any) -> AsyncIterator[AIMessageChunk]:
        self.seen.append(list(messages))
        self.calls.append(dict(kwargs))
        for delta in self.deltas:
            yield AIMessageChunk(content=delta)
        yield AIMessageChunk(content="", usage_metadata={"input_tokens": 11, "output_tokens": 22, "total_tokens": 33})


def _client(stub: _Stub) -> LlmClient:
    """注入桩模型（走 ``_clients`` 缓存，跳过真实厂商客户端构造）。"""
    client = LlmClient(base_url="http://127.0.0.1:9", model=MODEL)
    client._clients[MODEL] = stub
    return client


async def test_messages_reach_model_in_order() -> None:
    """消息原样透传、顺序不变；返回正文清掉首尾空白。"""
    stub = _Stub()

    answer = await _client(stub).chat(
        [
            SystemMessage(content="系统约束"),
            HumanMessage(content="上一轮问题"),
            AIMessage(content="上一轮回答"),
            HumanMessage(content="本轮问题"),
        ]
    )

    assert answer == "答案", "首尾空白应被清掉"
    assert [type(message).__name__ for message in stub.seen[0]] == [
        "SystemMessage",
        "HumanMessage",
        "AIMessage",
        "HumanMessage",
    ]


async def test_stream_maps_delta_and_usage() -> None:
    """正文逐片透传，用量在末片覆盖；``stream_chat`` 只出正文（空串分片不外泄）。"""
    client = _client(_Stub(deltas=("你", "好")))

    chunks: list[ChatStreamChunk] = [chunk async for chunk in client.stream_chat_events([HumanMessage(content="问题")])]
    assert "".join(chunk.delta for chunk in chunks) == "你好"
    assert (chunks[-1].tokens_in, chunks[-1].tokens_out) == (11, 22)

    assert [text async for text in client.stream_chat([HumanMessage(content="问题")])] == ["你", "好"]


async def test_readable_errors_are_not_downgraded() -> None:
    """空回答归一为 502；未配置地址是 503 且提示变量名——不得被兜底成「上游调用失败」。"""
    with pytest.raises(CustomException) as empty:
        await _client(_Stub(answer="   ")).chat([HumanMessage(content="问题")])
    assert empty.value.status_code == status.HTTP_502_BAD_GATEWAY

    with pytest.raises(CustomException) as unconfigured:
        await LlmClient(base_url="", model=MODEL).chat([HumanMessage(content="问题")])
    assert unconfigured.value.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert "LLM_BASE_URL" in unconfigured.value.msg


def test_chat_model_is_cached_per_model_name() -> None:
    """``chat_model()`` 是 create_agent 的取用口：同一模型名复用同一实例。"""
    stub = _Stub()
    client = _client(stub)

    assert client.chat_model() is stub
    assert client.chat_model(MODEL) is stub


async def test_trace_config_reaches_both_model_calls() -> None:
    """``config`` 必须透传到一次性与流式两条底层调用：追踪树靠它把模型 run 挂回本轮。

    本层不自己造 ``run_name``/``metadata``（那是 ``core.observability`` 的口径），
    只负责原样转发；顺带固定「不给 config 时传 None」——不能隐式自带一套默认值。
    """
    stub = _Stub(deltas=("好",))
    client = _client(stub)
    config = {"run_name": "kb.qa.generate", "metadata": {"mode": "stream"}}

    await client.chat([HumanMessage(content="问题")], config=config)
    _ = [chunk async for chunk in client.stream_chat_events([HumanMessage(content="问题")], config=config)]
    await client.chat([HumanMessage(content="问题")])

    assert [call.get("config") for call in stub.calls] == [config, config, None]
