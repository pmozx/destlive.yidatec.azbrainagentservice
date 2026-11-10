"""AI 问答的链路追踪（LangSmith）开关与薄封装。

三条口径：

1. **开关只由配置决定**：``AI_TRACE_ENABLE=false`` 时整条链路零上报——装饰器直通原函数，
   不建 run、不加 callback，业务侧不各自判断开关；
2. **配置必须在首次调用 LangChain 之前落到环境变量**：``langsmith.get_env_var`` 带
   ``lru_cache``，晚于首次读取再改 ``LANGSMITH_*`` 不会生效；启动期
   :func:`configure_tracing` 是唯一写入点（见 ``app/__init__.py`` 的 lifespan）；
3. **上送什么由脱敏开关决定**：``AI_TRACE_MASK_CONTENT=true`` 时隐藏 input/output/metadata
   的正文（``LANGSMITH_HIDE_*``），只留 run 树、耗时、token 用量与数值口径；关闭脱敏意味着
   问题、检索切片、模型回答会原样进入 LangSmith（合规由环境负责人确认）。

为什么直出档用 ``traceable`` 而不是套 agent 图：单轮 RAG 是「一次检索 → 一次生成」的
直出，没有分支决策，套一层图只会多一层消息协议与一批必须丢弃的中间帧。追踪只需要一个
包住整轮的父节点，``traceable`` 同时支持协程与异步生成器，SSE 帧序一行都不用改。
将来若引入图编排（团队规范 L24 决策档），图节点会自动建 run，节点名仍与本文件的 ``RUN_*`` 对齐。
"""

import functools
import hashlib
import inspect
import os
from collections.abc import AsyncIterator, Callable
from typing import Any, TypeVar

from app.config.setting import settings
from app.core.logger import logger

F = TypeVar("F", bound=Callable[..., Any])

#: run 名：LangSmith 里按它筛选整轮问答 / 检索 / 生成（决策档换 agent 图时节点沿用同名）
RUN_TURN = "kb.qa.turn"
RUN_RETRIEVE = "kb.qa.retrieve"
RUN_GENERATE = "kb.qa.generate"

#: 已包装过的函数缓存：``traceable`` 的包装对象按函数复用，避免每次调用重建
_TRACED: dict[tuple[Callable[..., Any], str], Callable[..., Any]] = {}


def tracing_enabled() -> bool:
    """追踪是否开启（只看配置，不看环境变量——环境变量是本模块写出去的结果）。"""
    return bool(settings.AI_TRACE_ENABLE)


def configure_tracing() -> bool:
    """把 ``AI_TRACE_*`` 写进 ``LANGSMITH_*`` 环境变量，返回最终是否开启。

    未配 API Key 时**失败关闭**：宁可不采集，也不要在一个「以为在采集」的状态下跑，
    那种状态最危险——出了问题却没有任何 trace 可查。
    """
    enabled = tracing_enabled()
    api_key = settings.AI_TRACE_API_KEY.strip()
    if enabled and not api_key:
        logger.warning("AI 追踪已开启但 AI_TRACE_API_KEY 为空，本轮进程按关闭处理（不会上报）")
        enabled = False

    os.environ["LANGSMITH_TRACING"] = "true" if enabled else "false"
    os.environ["LANGSMITH_PROJECT"] = settings.AI_TRACE_PROJECT
    # 隐藏正文的三个开关显式写死：宿主机导出的同名变量可能残留上一套口径
    for name in ("LANGSMITH_HIDE_INPUTS", "LANGSMITH_HIDE_OUTPUTS", "LANGSMITH_HIDE_METADATA"):
        os.environ[name] = "true" if settings.AI_TRACE_MASK_CONTENT else "false"
    if settings.AI_TRACE_ENDPOINT:
        os.environ["LANGSMITH_ENDPOINT"] = settings.AI_TRACE_ENDPOINT
    if api_key:
        os.environ["LANGSMITH_API_KEY"] = api_key

    # 配置可能在本次进程已读过一次（如测试、热重载）：清缓存让新的环境变量立即生效
    try:
        from langsmith.utils import get_env_var

        get_env_var.cache_clear()
    except Exception:  # noqa: BLE001 未装 langsmith 时追踪本来就用不了，不阻断启动
        logger.warning("langsmith 不可用，AI 追踪实际不会上报")

    if enabled:
        logger.info(
            "==== AI 追踪就绪：project={} endpoint={} 脱敏={} ====",
            settings.AI_TRACE_PROJECT,
            settings.AI_TRACE_ENDPOINT or "官方默认",
            "开" if settings.AI_TRACE_MASK_CONTENT else "关（正文会上送）",
        )
    return enabled


def traced(name: str, *, tags: tuple[str, ...] = ()) -> Callable[[F], F]:
    """给一轮问答挂追踪根节点；关闭时原样返回函数（零包装、零建 run）。

    支持协程与异步生成器两种入口：``completion`` 与 ``completion_stream`` 共用这里的口径，
    不必各自复制一份「开关判断 + 建 run」。
    """

    def decorate(func: F) -> F:
        if inspect.isasyncgenfunction(func):

            @functools.wraps(func)
            async def stream_wrapper(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
                if not tracing_enabled():
                    async for item in func(*args, **kwargs):
                        yield item
                    return
                async for item in _variant(func, name, tags)(*args, **kwargs):
                    yield item

            return stream_wrapper  # type: ignore[return-value]

        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            if not tracing_enabled():
                return await func(*args, **kwargs)
            return await _variant(func, name, tags)(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorate


def _variant(func: Callable[..., Any], name: str, tags: tuple[str, ...]) -> Callable[..., Any]:
    """取（或建）``traceable`` 包装对象：按函数 + run 名缓存，避免每轮重建。"""
    key = (func, name)
    cached = _TRACED.get(key)
    if cached is None:
        from langsmith import traceable

        cached = traceable(name=name, run_type="chain", tags=list(tags))(func)
        _TRACED[key] = cached
    return cached


def trace_metadata(**values: Any) -> None:
    """给当前 run 补数值口径（证据条数、模型名、门限）：脱敏只隐藏内容字段，这些仍会保留。"""
    if not tracing_enabled():
        return
    from langsmith.run_helpers import get_current_run_tree

    run_tree = get_current_run_tree()
    if run_tree is None:
        return
    metadata = run_tree.extra.setdefault("metadata", {})
    metadata.update({key: value for key, value in values.items() if value is not None})


def current_trace_id() -> str | None:
    """当前 trace 的 id（未开启追踪或不在 trace 内返回 None）：指标表据此回查 LangSmith。"""
    if not tracing_enabled():
        return None
    from langsmith.run_helpers import get_current_run_tree

    run_tree = get_current_run_tree()
    return None if run_tree is None else str(run_tree.trace_id)


def llm_config(name: str, **metadata: Any) -> dict[str, Any]:
    """模型调用的 ``RunnableConfig``：让模型 run 挂在当前链路上并带节点名（关闭时为空）。"""
    if not tracing_enabled():
        return {}
    return {"run_name": name, "metadata": {key: value for key, value in metadata.items() if value is not None}}


def user_pseudonym(user_id: int | str) -> str:
    """用户标识的伪名：按行聚合用，不把账号原值送进外部 SaaS。"""
    return hashlib.sha256(f"kb-user:{user_id}".encode("utf-8")).hexdigest()[:12]
