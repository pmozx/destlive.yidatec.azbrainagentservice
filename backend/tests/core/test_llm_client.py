"""聊天模型客户端（``app.core.clients.llm``）的抽象层回归测试。

守住五条：① 标准消息按顺序原样送进模型（本层不做二次包装）；
② 末帧 usage 映射到 ``ChatStreamChunk``（落库 token 统计依赖它）；
③ 未配置 / 空回答仍是可读异常，且 503 不被兜底成 502；
④ 调用方给的 ``config`` 原样透传到模型调用（不透传时模型 run 会成为 trace 里的孤立根节点）；
⑤ 构造参数按「档案参数 + 单次覆盖」合并，且缓存键把这些参数算进去——换参数不能复用实例。
不触达真实模型服务：桩替换 ``init_chat_model`` 的返回值。
"""

from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Any

import pytest
from fastapi import status
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage, SystemMessage

from app.config.setting import settings
from app.core.agents import PURPOSE_KB_ANSWER, ModelProfile, profile_for
from app.core.clients import llm as llm_module
from app.core.clients.llm import ChatStreamChunk, LlmClient
from app.core.exceptions import CustomException

MODEL = "stub-model"
BASE_URL = "http://127.0.0.1:9"


def _profile() -> ModelProfile:
    """纯 env 档案：本文件只验客户端，不查配置表。"""
    return profile_for(PURPOSE_KB_ANSWER, model=MODEL)


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


def _client(monkeypatch: pytest.MonkeyPatch, stub: _Stub) -> LlmClient:
    """桩掉 ``init_chat_model``：``chat_model`` 的参数组装照跑，只是不建真的厂商客户端。"""
    monkeypatch.setattr(llm_module, "init_chat_model", lambda *args, **kwargs: stub)
    return LlmClient(base_url=BASE_URL)


async def test_messages_reach_model_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """消息原样透传、顺序不变；返回正文清掉首尾空白。"""
    stub = _Stub()

    answer = await _client(monkeypatch, stub).chat(
        [
            SystemMessage(content="系统约束"),
            HumanMessage(content="上一轮问题"),
            AIMessage(content="上一轮回答"),
            HumanMessage(content="本轮问题"),
        ],
        profile=_profile(),
    )

    assert answer == "答案", "首尾空白应被清掉"
    assert [type(message).__name__ for message in stub.seen[0]] == [
        "SystemMessage",
        "HumanMessage",
        "AIMessage",
        "HumanMessage",
    ]


async def test_stream_maps_delta_and_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    """正文逐片透传，用量在末片覆盖；``stream_chat`` 只出正文（空串分片不外泄）。"""
    client = _client(monkeypatch, _Stub(deltas=("你", "好")))
    profile = _profile()

    chunks: list[ChatStreamChunk] = [
        chunk async for chunk in client.stream_chat_events([HumanMessage(content="问题")], profile=profile)
    ]
    assert "".join(chunk.delta for chunk in chunks) == "你好"
    assert (chunks[-1].tokens_in, chunks[-1].tokens_out) == (11, 22)

    assert [text async for text in client.stream_chat([HumanMessage(content="问题")], profile=profile)] == ["你", "好"]


async def test_readable_errors_are_not_downgraded(monkeypatch: pytest.MonkeyPatch) -> None:
    """空回答归一为 502；未配置地址是 503 且提示变量名——不得被兜底成「上游调用失败」。"""
    with pytest.raises(CustomException) as empty:
        await _client(monkeypatch, _Stub(answer="   ")).chat([HumanMessage(content="问题")], profile=_profile())
    assert empty.value.status_code == status.HTTP_502_BAD_GATEWAY

    # 档案与 env 都没有地址：这是部署缺配置，不是上游故障
    with pytest.raises(CustomException) as unconfigured:
        await LlmClient(base_url="", api_key="k").chat([HumanMessage(content="问题")], profile=_profile())
    assert unconfigured.value.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert "LLM_BASE_URL" in unconfigured.value.msg


async def test_chat_model_merges_profile_params_and_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    """构造参数 = 档案参数 + 单次覆盖；缓存按「模型 + 端点 + 密钥 + 全部参数」分桶。

    同参数复用实例（省一次 SDK 构造），覆盖参数则是另一份请求配置——复用会让前一次
    调用静默改掉后一次的设定。
    """
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_init(model: str, **kwargs: Any) -> object:
        calls.append((model, dict(kwargs)))
        return object()

    monkeypatch.setattr(llm_module, "init_chat_model", fake_init)
    client = LlmClient(base_url=BASE_URL)
    profile = _profile()

    first = client.chat_model(profile)
    assert client.chat_model(profile) is first, "同档案同参数必须复用实例"
    assert len(calls) == 1

    model, kwargs = calls[0]
    assert model == MODEL
    assert kwargs["model_provider"] == "openai"
    assert kwargs["base_url"] == BASE_URL
    assert kwargs["streaming"] is True and kwargs["stream_usage"] is True
    assert kwargs["temperature"] == settings.KB_ANSWER_TEMPERATURE
    assert kwargs["max_tokens"] == settings.KB_ANSWER_MAX_TOKENS
    assert kwargs["timeout"] == settings.LLM_TIMEOUT
    assert kwargs["max_retries"] == settings.LLM_MAX_RETRIES

    overridden = client.chat_model(profile, overrides={"max_tokens": 100})
    assert overridden is not first, "覆盖参数是另一份请求配置，不能复用实例"
    assert calls[1][1]["max_tokens"] == 100
    assert calls[1][1]["temperature"] == settings.KB_ANSWER_TEMPERATURE, "覆盖只能改自己声明的键"

    await client.aclose()


async def test_trace_config_reaches_both_model_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """``config`` 必须透传到一次性与流式两条底层调用：追踪树靠它把模型 run 挂回本轮。

    本层不自己造 ``run_name``/``metadata``（那是 ``core.observability`` 的口径），
    只负责原样转发；顺带固定「不给 config 时传 None」——不能隐式自带一套默认值。
    """
    stub = _Stub(deltas=("好",))
    client = _client(monkeypatch, stub)
    profile = _profile()
    config = {"run_name": "kb.qa.generate", "metadata": {"mode": "stream"}}

    await client.chat([HumanMessage(content="问题")], profile=profile, config=config)
    _ = [chunk async for chunk in client.stream_chat_events([HumanMessage(content="问题")], profile=profile, config=config)]
    await client.chat([HumanMessage(content="问题")], profile=profile)

    assert [call.get("config") for call in stub.calls] == [config, config, None]


async def test_local_provider_is_normalized_to_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    """本地部署（vLLM / TEI / llama.cpp）走 OpenAI 兼容协议：provider 透传会报 Unsupported provider。"""
    calls: list[dict[str, Any]] = []

    def fake_init(model: str, **kwargs: Any) -> object:
        calls.append(dict(kwargs))
        return object()

    monkeypatch.setattr(llm_module, "init_chat_model", fake_init)
    client = LlmClient(base_url=BASE_URL)
    # 档案里的 provider 是配置表给的：这里直接换成页面会让人填的本地写法
    local = replace(_profile(), provider="vllm")

    assert llm_module.chat_provider("local") == "openai"
    assert llm_module.chat_provider("") == "openai"
    assert llm_module.chat_provider("ollama") == "ollama", "Ollama 协议不同，不能被归一"

    local_model = client.chat_model(local)
    assert calls[0]["model_provider"] == "openai"
    assert client.chat_model(_profile()) is local_model, "归一后与 openai 写法同缓存键"

    await client.aclose()
