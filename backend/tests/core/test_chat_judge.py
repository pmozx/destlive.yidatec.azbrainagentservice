"""证据充分性二判（``app.core.chat.judge``）回归。

守住三条：

1. **结构化输出**：判定必须走 ``with_structured_output``（拿布尔结论，而不是让模型自由发挥一段话
   再正则解析）；
2. **失败不升级**：上游异常、超时、结构不可解析一律返回「够答」——判定是增强，不是门禁，
   因为质检挂了而拒答等于把可用系统降级成不可用；
3. 判定看的就是提示词里那份资料（``recall`` 原样送进模型，不二次加工）。
"""

import asyncio
from typing import Any

import pytest

from app.config.setting import settings
from app.core.chat import judge as judge_module
from app.core.chat.judge import SufficiencyVerdict, judge_sufficiency

RECALL = "参考资料：\n[1] 开通流程\n在服务台提交申请，由负责人开通。\n\n"


class _FakeModel:
    """桩模型：``outcome`` 是返回值或要抛出的异常；记录收到的消息与绑定过的 schema。"""

    def __init__(self, outcome: Any, *, delay: float = 0.0) -> None:
        self.outcome = outcome
        self.delay = delay
        self.seen: list[list[Any]] = []
        self.schema: Any = None

    def with_structured_output(self, schema: Any) -> "_FakeModel":
        self.schema = schema
        return self

    async def ainvoke(self, messages: list[Any]) -> Any:
        self.seen.append(list(messages))
        if self.delay:
            await asyncio.sleep(self.delay)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class _FakeClient:
    def __init__(self, model: _FakeModel) -> None:
        self._model = model

    def chat_model(self, profile: Any, *, overrides: Any = None) -> _FakeModel:  # noqa: ANN401 - 与 LlmClient 同签名
        return self._model


def _install(monkeypatch: pytest.MonkeyPatch, model: _FakeModel) -> _FakeModel:
    monkeypatch.setattr(judge_module, "get_llm_client", lambda: _FakeClient(model))
    return model


async def test_insufficient_verdict_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    """模型判「不足以回答」时回传 ``False`` 与缺什么，且资料原样进了消息。"""
    model = _install(monkeypatch, _FakeModel(SufficiencyVerdict(sufficient=False, missing="缺少审批人")))

    sufficient, missing = await judge_sufficiency("如何申请 VPN？", RECALL)

    assert sufficient is False
    assert missing == "缺少审批人"
    assert model.schema is SufficiencyVerdict, "必须绑定结构化输出，不能靠解析自由文本"
    content = model.seen[0][-1].content
    assert RECALL.strip() in content
    assert "如何申请 VPN？" in content


async def test_upstream_error_falls_back_to_sufficient(monkeypatch: pytest.MonkeyPatch) -> None:
    """上游异常按「够答」处理：质检失败不能变成拒答。"""
    _install(monkeypatch, _FakeModel(RuntimeError("模型服务 502")))

    assert await judge_sufficiency("问题", RECALL) == (True, "")


async def test_timeout_falls_back_to_sufficient(monkeypatch: pytest.MonkeyPatch) -> None:
    """二判超时同样按「够答」处理（省下来的调用不该换来一次拒答）。"""
    monkeypatch.setattr(settings, "KB_GRADE_JUDGE_TIMEOUT", 0.01)
    _install(monkeypatch, _FakeModel(SufficiencyVerdict(sufficient=False), delay=1.0))

    assert await judge_sufficiency("问题", RECALL) == (True, "")


async def test_dict_verdict_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """部分 provider 的 json 模式只回 dict：能还原就照用，还原失败才降级。"""
    _install(monkeypatch, _FakeModel({"sufficient": False, "missing": "只覆盖边缘条件"}))

    assert await judge_sufficiency("问题", RECALL) == (False, "只覆盖边缘条件")


async def test_unparsable_verdict_falls_back_to_sufficient(monkeypatch: pytest.MonkeyPatch) -> None:
    """结构对不上（既不是模型也不是可还原的 dict）时降级为「够答」。"""
    _install(monkeypatch, _FakeModel("资料看起来还行"))

    assert await judge_sufficiency("问题", RECALL) == (True, "")
