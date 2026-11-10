"""模型配置表 → 模型档案（``app.core.agents.registry`` / ``.profiles``）的回归测试。

守住四条：

1. **逐键覆盖**：env 兜底 < 配置表 ``default_config_json`` < 用途场景 < 单次调用——
   表里没写的键必须沿用 env，不能整份替换（否则老部署接上配置表后生成效果静默改变）；
2. **白名单**：连接类、流式开关等键不认，丢弃并告警（一行手填配置不该能把网关改走）；
3. **三条回落**：命中 → 行数据；未命中 → 按模型名直传；表不可用 → 纯 env，
   且失败后 TTL 内不再反复查库（不能让每个请求都等一次数据库超时）；
4. 槽位为空时才用表里 ``is_default`` 的行；显式传了 ``key`` 就不能被兜底盖掉。

不连数据库：``list_active`` 与 ``async_session_factory`` 都是桩。
"""

from types import SimpleNamespace
from typing import Any

import pytest

from app.config.setting import settings
from app.core.agents import profiles as profiles_module
from app.core.agents import registry as registry_module
from app.core.agents.profiles import (
    PURPOSE_KB_ANSWER,
    PURPOSE_PARSE_FAQ,
    filter_config,
    merge_params,
)
from app.core.agents.registry import get_model_config_registry, load_profile, reset_model_config_registry
from app.modules.system.model_config.crud import SystemModelConfigCRUD


class _Recorder:
    """替身 logger：只记日志文本，便于断言「丢弃/回落有没有留痕」。"""

    def __init__(self) -> None:
        self.warnings: list[str] = []
        self.infos: list[str] = []

    def warning(self, message: str, *args: Any) -> None:
        self.warnings.append(message.format(*args))

    def info(self, message: str, *args: Any) -> None:
        self.infos.append(message.format(*args))


def _row(**overrides: Any) -> SimpleNamespace:
    """一行配置表记录（只带注册表会读到的列）。"""
    base = {
        "model_config_key": "deepseek-flash",
        "model_config_name": "DeepSeek 快速档",
        "model_type": "chat",
        "model_name": "deepseek-ai/DeepSeek-V4-Flash",
        "model_provider": "openai",
        "base_url": "http://gateway.local/v1",
        "api_key": "sk-row",
        "default_config_json": {"temperature": 0.7, "max_tokens": 800},
        "is_default": False,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class _FakeSession:
    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False


def _install(monkeypatch: pytest.MonkeyPatch, rows: list[SimpleNamespace]) -> list[int]:
    """把查库换成返回 ``rows``；返回的列表长度即「实际查了几次库」。"""
    queries: list[int] = []

    async def fake_list_active(self: Any) -> list[SimpleNamespace]:
        queries.append(1)
        return list(rows)

    monkeypatch.setattr(registry_module, "async_session_factory", _FakeSession)
    # 注册表在刷新时才导入 CRUD（避免 core↔modules 包级环），因此桩打在 CRUD 类上
    monkeypatch.setattr(SystemModelConfigCRUD, "list_active", fake_list_active)
    return queries


@pytest.fixture(autouse=True)
def _fresh_registry() -> Any:
    """每个用例一份干净快照：进程级单例不能把上一个用例的配置行带进来。"""
    reset_model_config_registry()
    yield
    reset_model_config_registry()


async def test_invalidate_forces_one_reload(monkeypatch: pytest.MonkeyPatch) -> None:
    """页面改完配置要即时生效：打完过期标记后下一次取档案必须重查一次表。

    只标记不立即查库是刻意的（写请求还在事务里），所以这里断言的是「下一次查询会多一次」，
    不是「立刻查」。
    """
    queries = _install(monkeypatch, [_row()])
    monkeypatch.setattr(settings, "LLM_CONFIG_CACHE_TTL", 3600.0)
    monkeypatch.setattr(settings, "LLM_MODEL_KB_ANSWER", "deepseek-flash")

    await load_profile(PURPOSE_KB_ANSWER)
    await load_profile(PURPOSE_KB_ANSWER)
    assert len(queries) == 1, "TTL 内复用快照"

    registry_module.invalidate_model_config_cache()

    await load_profile(PURPOSE_KB_ANSWER)
    assert len(queries) == 2, "过期标记后必须重查一次"


def test_merge_keeps_env_for_keys_the_table_omits(monkeypatch: pytest.MonkeyPatch) -> None:
    """表里只写了 temperature 时，max_tokens 仍取 env——逐键兜底而非整份替换。"""
    monkeypatch.setattr(settings, "KB_ANSWER_TEMPERATURE", 0.2)
    monkeypatch.setattr(settings, "KB_ANSWER_MAX_TOKENS", 1500)

    merged: dict[str, Any] = merge_params(PURPOSE_KB_ANSWER, {"temperature": 0.7})

    assert merged == {"temperature": 0.7, "max_tokens": 1500}


def test_scene_params_beat_table_defaults() -> None:
    """用途场景写死在代码里，覆盖模型级默认值（FAQ 抽取必须低温）。"""
    merged: dict[str, Any] = merge_params(PURPOSE_PARSE_FAQ, {"temperature": 0.9, "top_p": 0.5})

    assert merged["temperature"] == 0.1
    assert merged["top_p"] == 0.5, "场景没声明的键照常沿用表值"


def test_unknown_config_keys_are_dropped_with_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    """连接类与流式开关不在白名单：丢弃并告警，不能让它改掉网关或落库前置条件。"""
    recorder = _Recorder()
    monkeypatch.setattr(profiles_module, "logger", recorder)

    kept = filter_config(
        {"temperature": 0.5, "streaming": False, "base_url": "http://evil.local/v1"},
        source="test",
    )

    assert kept == {"temperature": 0.5}
    assert len(recorder.warnings) == 2
    assert all("已丢弃" in text for text in recorder.warnings)


async def test_matching_row_supplies_connection_and_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """命中 ``model_config_key``：模型标识与厂商模型名分开，连接方式与默认参数全部来自行。"""
    _install(monkeypatch, [_row()])
    monkeypatch.setattr(settings, "LLM_MODEL_KB_ANSWER", "deepseek-flash")

    profile = await load_profile(PURPOSE_KB_ANSWER)

    assert (profile.key, profile.model) == ("deepseek-flash", "deepseek-ai/DeepSeek-V4-Flash")
    assert profile.label == "deepseek-flash", "指标按模型标识归集"
    assert (profile.provider, profile.base_url, profile.api_key) == ("openai", "http://gateway.local/v1", "sk-row")
    assert profile.params["temperature"] == 0.7
    assert profile.params["max_tokens"] == 800
    assert profile.timeout == settings.KB_LLM_TIMEOUT


async def test_empty_table_defaults_keep_env_sampling(monkeypatch: pytest.MonkeyPatch) -> None:
    """行里 ``default_config_json`` 为空时参数必须是 env 那套：接入配置表不该改变生成效果。"""
    _install(monkeypatch, [_row(default_config_json={})])
    monkeypatch.setattr(settings, "LLM_MODEL_KB_ANSWER", "deepseek-flash")
    monkeypatch.setattr(settings, "KB_ANSWER_TEMPERATURE", 0.2)
    monkeypatch.setattr(settings, "KB_ANSWER_MAX_TOKENS", 1500)

    profile = await load_profile(PURPOSE_KB_ANSWER)

    assert profile.params["temperature"] == 0.2
    assert profile.params["max_tokens"] == 1500


async def test_unmatched_slot_is_passed_as_model_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """表里没有该标识：把槽位值当模型名直传（兼容 v2.2 之前的 env 口径），并告警留痕。"""
    recorder = _Recorder()
    monkeypatch.setattr(registry_module, "logger", recorder)
    _install(monkeypatch, [_row()])
    monkeypatch.setattr(settings, "LLM_MODEL_KB_ANSWER", "legacy-model")

    profile = await load_profile(PURPOSE_KB_ANSWER)

    assert (profile.key, profile.model) == ("legacy-model", "legacy-model")
    assert profile.base_url == "", "老部署的网关仍在 env 里"
    assert any("按模型名直传" in text for text in recorder.warnings)


async def test_explicit_key_is_not_overridden_by_default_row(monkeypatch: pytest.MonkeyPatch) -> None:
    """显式指定了标识就必须用它：调用方要的模型不能被 ``is_default`` 那行悄悄换掉。"""
    _install(monkeypatch, [_row(model_config_key="fast", is_default=True)])

    profile = await load_profile(PURPOSE_PARSE_FAQ, key="gsi-missing")

    assert profile.key == "gsi-missing"


async def test_empty_slot_uses_the_default_row(monkeypatch: pytest.MonkeyPatch) -> None:
    """槽位全空（env 没配）= 「表里配好就能上线」：取 ``is_default`` 那一行。"""
    _install(
        monkeypatch,
        [_row(model_config_key="other"), _row(model_config_key="fast", is_default=True)],
    )
    monkeypatch.setattr(settings, "LLM_MODEL_KB_ANSWER", "")
    monkeypatch.setattr(settings, "LLM_MODEL", "")

    profile = await load_profile(PURPOSE_KB_ANSWER)

    assert profile.key == "fast"


async def test_unavailable_table_falls_back_to_env_without_retry_storm(monkeypatch: pytest.MonkeyPatch) -> None:
    """表还建不出来（DDL 未执行）时按 env 跑，且 TTL 内不再重试。"""
    recorder = _Recorder()
    monkeypatch.setattr(registry_module, "logger", recorder)
    queries: list[int] = []

    async def boom(self: Any) -> list[Any]:
        queries.append(1)
        raise RuntimeError('column "model_name" does not exist')

    monkeypatch.setattr(registry_module, "async_session_factory", _FakeSession)
    monkeypatch.setattr(SystemModelConfigCRUD, "list_active", boom)
    monkeypatch.setattr(settings, "LLM_CONFIG_CACHE_TTL", 60.0)
    monkeypatch.setattr(settings, "LLM_MODEL_KB_ANSWER", "legacy-model")

    for _ in range(3):
        assert (await load_profile(PURPOSE_KB_ANSWER)).model == "legacy-model"

    assert len(queries) == 1, "失败也要退避：不能让每个请求都等一次数据库超时"
    assert any("回落 env" in text for text in recorder.warnings)


async def test_disabled_switch_never_queries_table(monkeypatch: pytest.MonkeyPatch) -> None:
    """``LLM_CONFIG_ENABLE=False`` 时连表都不查：可一键退回纯 env 口径。"""
    queries = _install(monkeypatch, [_row()])
    monkeypatch.setattr(settings, "LLM_CONFIG_ENABLE", False)
    monkeypatch.setattr(settings, "LLM_MODEL_KB_ANSWER", "deepseek-flash")

    profile = await load_profile(PURPOSE_KB_ANSWER)

    assert (profile.model, profile.base_url) == ("deepseek-flash", "")
    assert queries == []


def test_chat_type_constant_matches_model() -> None:
    """registry 在 core 层不能反向 import modules，只能靠这条测试钉住两边常量一致。"""
    from app.modules.system.model_config.model import MODEL_TYPE_CHAT

    assert registry_module.CHAT_MODEL_TYPE == MODEL_TYPE_CHAT


async def test_default_row_is_scoped_by_type(monkeypatch: pytest.MonkeyPatch) -> None:
    """每类各自一个默认行：embedding 的默认不能被 chat 的兜底取走，反之亦然。"""
    _install(
        monkeypatch,
        [
            _row(model_config_key="chat-default", is_default=True),
            _row(model_config_key="embed-default", model_type="embedding", is_default=True),
        ],
    )

    registry = get_model_config_registry()
    await registry.ensure_loaded()

    assert registry.size == 2, "快照收全部类型"
    assert registry.default_row("chat").key == "chat-default"
    assert registry.default_row("embedding").key == "embed-default"
    assert registry.default_row("rerank") is None


async def test_embedding_rows_never_supply_chat_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    """嵌入 / 重排序行只供客户端工厂取默认行：当成对话模型用，第一个请求就会「模型不存在」。"""
    _install(
        monkeypatch,
        [
            _row(model_config_key="bge-m3", model_type="embedding", is_default=True, base_url="http://embed.local/v1"),
            _row(model_config_key="bge-reranker", model_type="rerank"),
        ],
    )
    monkeypatch.setattr(settings, "LLM_MODEL_KB_ANSWER", "bge-m3")
    monkeypatch.setattr(settings, "LLM_MODEL", "")

    profile = await load_profile(PURPOSE_KB_ANSWER)

    assert profile.key == "bge-m3", "未命中对话行时按模型名直传（兼容旧 env 口径）"
    assert profile.base_url == "", "不能拿到 embedding 行的网关与默认参数"
