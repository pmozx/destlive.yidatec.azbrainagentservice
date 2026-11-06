"""AI 追踪开关（``app.core.observability``）的回归测试。

守住四条：

1. **关闭即零上报**：``AI_TRACE_ENABLE=false`` 时 ``LANGSMITH_TRACING`` 写成 ``false``，且
   ``traced`` 不构造 traceable 包装（每轮都建 run 就是纯开销）；
2. **没配 Key 就失败关闭**：不能停在「以为在采集、实际没上报」的半开状态——那种状态最危险，
   出了问题却没有任何 trace 可查；
3. **脱敏是三个开关一起拧**：``LANGSMITH_HIDE_INPUTS/OUTPUTS/METADATA`` 必须同时 true 或 false，
   不能留一半（只隐藏其中一项等于正文照样出境）；
4. **送出去的只有伪名与数值口径**：``user_pseudonym`` 是不可逆哈希，不携带账号原值。

不触达 LangSmith 服务：只验写入的环境变量与包装路由，真实上报由开发环境联调确认。
"""

import os

import pytest

from app.config.setting import settings
from app.core.observability import (
    RUN_GENERATE,
    RUN_TURN,
    configure_tracing,
    current_trace_id,
    llm_config,
    trace_metadata,
    traced,
    user_pseudonym,
)
from app.core.observability import tracing as tracing_module

#: ``configure_tracing`` 会写的环境变量：整个模块统一纳入快照，用例结束后还原
_TRACE_ENV = (
    "LANGSMITH_TRACING",
    "LANGSMITH_PROJECT",
    "LANGSMITH_ENDPOINT",
    "LANGSMITH_API_KEY",
    "LANGSMITH_HIDE_INPUTS",
    "LANGSMITH_HIDE_OUTPUTS",
    "LANGSMITH_HIDE_METADATA",
)


@pytest.fixture(autouse=True)
def _isolate_trace_env(monkeypatch: pytest.MonkeyPatch):
    """环境变量快照：被测代码直接写 ``os.environ``，只能靠 ``monkeypatch`` 记下原值再还原。"""
    for name in _TRACE_ENV:
        monkeypatch.setenv(name, "snapshot")


def test_disabled_writes_closed_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """关闭态：写 ``false`` + 脱敏开关为真；此时 ``tracing_enabled()`` 也是假。"""
    monkeypatch.setattr(settings, "AI_TRACE_ENABLE", False)
    monkeypatch.setattr(settings, "AI_TRACE_PROJECT", "brain-test")
    monkeypatch.setattr(settings, "AI_TRACE_MASK_CONTENT", True)

    assert configure_tracing() is False
    assert tracing_module.tracing_enabled() is False
    assert os.environ["LANGSMITH_TRACING"] == "false"
    assert os.environ["LANGSMITH_PROJECT"] == "brain-test"
    assert os.environ["LANGSMITH_HIDE_INPUTS"] == "true"
    assert os.environ["LANGSMITH_HIDE_OUTPUTS"] == "true"
    assert os.environ["LANGSMITH_HIDE_METADATA"] == "true"


def test_missing_api_key_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """开了开关但没配 Key：按关闭处理，并把可能残留的 ``true`` 改回 ``false``。"""
    monkeypatch.setattr(settings, "AI_TRACE_ENABLE", True)
    monkeypatch.setattr(settings, "AI_TRACE_API_KEY", "   ")
    os.environ["LANGSMITH_TRACING"] = "true"

    assert configure_tracing() is False
    assert os.environ["LANGSMITH_TRACING"] == "false"


def test_enabled_with_key_writes_true_and_follows_mask_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    """开启态：写 ``true`` 与 Key；脱敏关掉时三个 HIDE 必须一起变 ``false``。"""
    monkeypatch.setattr(settings, "AI_TRACE_ENABLE", True)
    monkeypatch.setattr(settings, "AI_TRACE_API_KEY", "lsv2-test")
    # 指向回环：即便监控线程真的发请求也不会出网
    monkeypatch.setattr(settings, "AI_TRACE_ENDPOINT", "http://127.0.0.1:9")
    monkeypatch.setattr(settings, "AI_TRACE_MASK_CONTENT", False)

    assert configure_tracing() is True
    assert os.environ["LANGSMITH_TRACING"] == "true"
    assert os.environ["LANGSMITH_API_KEY"] == "lsv2-test"
    assert os.environ["LANGSMITH_ENDPOINT"] == "http://127.0.0.1:9"
    assert os.environ["LANGSMITH_HIDE_INPUTS"] == "false"
    assert os.environ["LANGSMITH_HIDE_OUTPUTS"] == "false"
    assert os.environ["LANGSMITH_HIDE_METADATA"] == "false"


async def test_traced_passes_through_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """关闭时 ``traced`` 只做直通：既不查环境也不构造 traceable 包装。"""
    monkeypatch.setattr(settings, "AI_TRACE_ENABLE", False)
    built: list[object] = []

    def _spy(func, name, tags):  # noqa: ANN001, ANN202 - 打桩不需要精确签名
        built.append(name)
        return func

    monkeypatch.setattr(tracing_module, "_variant", _spy)

    @traced(RUN_TURN, tags=("kb",))
    async def handler(value: str) -> str:
        return f"答：{value}"

    assert await handler("问题") == "答：问题"
    assert built == [], "关闭态不得构造 traceable 包装"


async def test_traced_routes_to_traceable_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """开启时协程与异步生成器都走 traceable 分支，且 run 名/标签按装饰器原样传入。"""
    monkeypatch.setattr(settings, "AI_TRACE_ENABLE", True)
    seen: list[tuple[str, tuple[str, ...]]] = []

    def _fake_variant(func, name, tags):  # noqa: ANN001, ANN202 - 原函数即「已包装」，只验路由
        seen.append((name, tags))
        return func

    monkeypatch.setattr(tracing_module, "_variant", _fake_variant)

    @traced(RUN_TURN, tags=("kb", "qa"))
    async def handler() -> str:
        return "ok"

    @traced(RUN_GENERATE, tags=("kb",))
    async def stream_handler():  # noqa: ANN202 - 被测对象是一个异步生成器
        yield "一"
        yield "二"

    assert await handler() == "ok"
    assert [item async for item in stream_handler()] == ["一", "二"]
    assert seen == [(RUN_TURN, ("kb", "qa")), (RUN_GENERATE, ("kb",))]


def test_llm_config_is_empty_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """关闭时返回空字典：调用方直接传给模型，不产生任何 run 语义。"""
    monkeypatch.setattr(settings, "AI_TRACE_ENABLE", False)

    assert llm_config(RUN_GENERATE, mode="stream") == {}


def test_llm_config_keeps_run_name_and_drops_empty_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """开启时带 ``run_name``；空值不进 metadata（否则 trace 里全是 None 噪声）。"""
    monkeypatch.setattr(settings, "AI_TRACE_ENABLE", True)

    assert llm_config(RUN_GENERATE, mode="stream", model=None) == {
        "run_name": RUN_GENERATE,
        "metadata": {"mode": "stream"},
    }


def test_trace_helpers_are_noops_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """关闭时补 metadata 不抛异常、trace id 为 None（没有 run 上下文是常态）。"""
    monkeypatch.setattr(settings, "AI_TRACE_ENABLE", False)

    trace_metadata(session_id="session-1")
    assert current_trace_id() is None


def test_user_pseudonym_is_stable_and_opaque() -> None:
    """伪名：12 位十六进制、同值稳定、int 与 str 不拆成两个人，且不携带账号原值。"""
    pseudonym = user_pseudonym(1001)

    assert len(pseudonym) == 12
    assert set(pseudonym) <= set("0123456789abcdef")
    assert pseudonym == user_pseudonym("1001")
    assert pseudonym not in {"1001", "kb-user:1001"}
