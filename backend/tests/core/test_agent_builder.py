"""智能体构建层（``app.core.agents``）的回归测试。

守住四条：① 用途槽位留空即回落 ``LLM_MODEL``，配了就覆盖（规范 14.1.2）；
② 构建时模型整份取自 ``LlmClient.chat_model(profile)``——不能把模型名丢给默认 provider，
那会绕过自建网关的地址与代理控制；也不能在这里二次转温度/上限，那会与配置表形成第二个口径；
③ 正文流只转发非空模型正文，
工具调用帧与运行结束的聚合帧不外泄；④ 用量按轮累加（agent 一轮可能调多次模型）。
不触达真实模型服务：桩替换 ``create_agent`` 与底层 ChatModel。
"""

from typing import Any

import pytest
from langchain_core.messages import AIMessageChunk, HumanMessage

from app.config.setting import settings
from app.core import agents
from app.core.agents import (
    PURPOSES,
    PURPOSE_GRADE_JUDGE,
    PURPOSE_KB_ANSWER,
    PURPOSE_PARSE_FAQ,
    profile_for,
    stream_agent_text,
)


def test_purposes_are_all_resolvable() -> None:
    """``PURPOSES`` 是唯一清单：每个用途都必须能取到档案（新增用途忘了写分支会在这里挂）。"""
    for purpose in PURPOSES:
        assert profile_for(purpose).purpose == purpose


def test_profile_falls_back_to_global_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """槽位留空 = 沿用 ``LLM_MODEL``：某用途忘了配不会变成「连不上」。"""
    monkeypatch.setattr(settings, "LLM_MODEL", "global-model")
    monkeypatch.setattr(settings, "LLM_MODEL_KB_ANSWER", "")

    profile = profile_for(PURPOSE_KB_ANSWER)

    assert profile.model == "global-model"
    assert profile.params["temperature"] == settings.KB_ANSWER_TEMPERATURE
    assert profile.params["max_tokens"] == settings.KB_ANSWER_MAX_TOKENS


def test_profile_slot_overrides_global_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """槽位配了就覆盖默认模型；二判不带温度（传值会改变既有生成行为）。"""
    monkeypatch.setattr(settings, "LLM_MODEL", "global-model")
    monkeypatch.setattr(settings, "LLM_MODEL_GRADE_JUDGE", "cheap-model")

    profile = profile_for(PURPOSE_GRADE_JUDGE)

    assert profile.model == "cheap-model"
    assert "temperature" not in profile.params, "没配的键不写进档案：由配置表/网关默认值决定"
    assert profile.timeout == settings.KB_GRADE_JUDGE_TIMEOUT


def test_profile_explicit_model_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    """显式模型（GSI 入参选择模型）优先于槽位；FAQ 抽取保持低温。"""
    monkeypatch.setattr(settings, "LLM_MODEL_PARSE_FAQ", "slot-model")

    assert profile_for(PURPOSE_PARSE_FAQ, model="gsi-model").model == "gsi-model"
    assert profile_for(PURPOSE_PARSE_FAQ).model == "slot-model"
    assert profile_for(PURPOSE_PARSE_FAQ).params["temperature"] == 0.1


def test_unknown_purpose_is_rejected() -> None:
    """未知用途是编码错误，必须当场炸掉而不是静默回落默认模型。"""
    with pytest.raises(ValueError, match="未知的模型用途"):
        profile_for("nope")


def test_build_agent_uses_client_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """构建必须走 ``chat_model``：模型实例带网关地址，而不是把模型名交给默认 provider。"""
    captured: dict[str, Any] = {}
    fake_model = object()

    class _Client:
        def chat_model(self, profile: Any, *, overrides: Any = None) -> object:
            captured["profile"] = (profile, overrides)
            return fake_model

    def fake_create_agent(**kwargs: Any) -> str:
        captured["agent"] = kwargs
        return "agent-graph"

    monkeypatch.setattr(agents.builder, "get_llm_client", lambda: _Client())
    monkeypatch.setattr(agents.builder, "create_agent", fake_create_agent)

    profile = profile_for(PURPOSE_KB_ANSWER)
    result = agents.build_agent(profile, system_prompt="系统约束")

    assert result == "agent-graph"
    assert captured["profile"] == (profile, None), "档案整份交出去：模型名、网关、默认参数都在里面"
    assert captured["agent"] == {
        "model": fake_model,
        "tools": None,
        "system_prompt": "系统约束",
        "response_format": None,
        "middleware": [],
        "name": PURPOSE_KB_ANSWER,
    }


class _FakeAgent:
    """假智能体图：只复现 ``astream(stream_mode="messages")`` 的产出形状。"""

    def __init__(self, frames: list[tuple[Any, dict[str, Any]]]) -> None:
        self.frames = frames
        self.seen: tuple[Any, Any] = (None, None)

    async def astream(self, inputs: Any, *, stream_mode: str, config: Any = None):
        assert stream_mode == "messages"
        self.seen = (inputs, config)
        for frame in self.frames:
            yield frame


async def test_stream_agent_text_filters_non_text_frames() -> None:
    """工具调用帧不外泄，但**只带用量的空正文帧必须保留**：它是最后一轮 token 的唯一出口。"""
    frames = [
        (AIMessageChunk(content=""), {}),  # 工具调用分片：有 tool_call_chunks，无正文
        (AIMessageChunk(content="结"), {}),
        (AIMessageChunk(content=[{"type": "text", "text": "论"}]), {}),  # LangChain 1.x 内容块
        (AIMessageChunk(content="", usage_metadata={"input_tokens": 5, "output_tokens": 7, "total_tokens": 12}), {}),
        (AIMessageChunk(content="第二"), {}),
        (AIMessageChunk(content="", usage_metadata={"input_tokens": 3, "output_tokens": 2, "total_tokens": 5}), {}),
    ]
    agent = _FakeAgent(frames)

    chunks = [chunk async for chunk in stream_agent_text(agent, [HumanMessage(content="问题")])]

    # 有正文的分片按序还原；内容块列表（LangChain 1.x）按同一口径归一
    assert [chunk.delta for chunk in chunks if chunk.delta] == ["结", "论", "第二"]
    # 用量按轮累加：末片覆盖的口径会只剩最后一轮的 (3, 2)
    assert (chunks[-1].tokens_in, chunks[-1].tokens_out) == (8, 9)
    # 末帧只带用量、不带正文：调用方据此更新指标而不发 delta，否则前端多出空段落
    assert chunks[-1].delta == ""
    assert agent.seen[0] == {"messages": [HumanMessage(content="问题")]}


async def test_stream_agent_text_passes_config() -> None:
    """``config`` 原样透传给图：不透传时模型 run 会成为 trace 里的孤立节点。"""
    agent = _FakeAgent([(AIMessageChunk(content="好"), {})])
    config = {"run_name": "kb.qa.agent", "metadata": {"mode": "stream"}}

    _ = [chunk async for chunk in stream_agent_text(agent, [HumanMessage(content="问题")], config=config)]

    assert agent.seen[1] == config
