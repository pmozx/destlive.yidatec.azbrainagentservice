"""按用途钉选的模型档案（规范 14.1.2 / L27 的配置侧落地）。

为什么需要这一层：``LLM_MODEL`` 只有一个默认槽位，而三个消费点的口径本就不同——问答要低温
且限 1500 token、二判要 8 秒硬超时且失败不升级、FAQ 提取要 300 秒预算并允许 GSI 按需指定
模型。这些参数原先散落在 settings 与各调用点，日志与指标只能看到「用了哪个模型」，看不到
「这条成本属于哪个用途」。

「怎么连、默认怎么采样」搬进配置表 ``sbp_ai_system_model_config``（基座自维护、页面可改），本模块只负责把
「用途」翻译成一份档案，并钉死参数的三级覆盖顺序。

四条边界：

1. **只做投影，不做第二份配置**：档案的值来自 settings 与配置表，本模块不新增真理源；
2. **回落链路固定**：配置表查不到 → 按 env 槽位当模型名直传 → 再空用 ``LLM_MODEL``；
3. **参数逐键覆盖**：env 兜底 < 配置表默认 ``default_config_json`` < 用途场景 < 单次调用；
4. **不决定调用方式**：档案描述「怎么调模型」，不决定「要不要套 agent」——单轮直出仍走
   ``LlmClient.chat``，需要工具决策时把档案交给 :func:`app.core.agents.build_agent`。
"""

from dataclasses import dataclass, field
from typing import Any, Mapping

from app.config.setting import settings
from app.core.logger import logger

#: 用途标识：同时作为日志、trace 与智能体图名的键；改名等于换口径，需全仓同步
PURPOSE_KB_ANSWER = "kb_answer"
PURPOSE_GRADE_JUDGE = "kb_grade_judge"
PURPOSE_PARSE_FAQ = "parse_faq"

#: 全部用途（错误提示与配置自检共用，避免两处清单不一致）
PURPOSES = (PURPOSE_KB_ANSWER, PURPOSE_GRADE_JUDGE, PURPOSE_PARSE_FAQ)

#: ``default_config_json`` 允许出现的键：``init_chat_model`` 透传给 ``ChatOpenAI`` 的
#: 采样与传输参数（对照 langchain 1.4.7 的 ``ChatOpenAI.model_fields`` 逐项核过）。
#: 连接类（base_url / api_key）、连接池（http_async_client）、流式开关（streaming /
#: stream_usage）不在表里配——它们由连接列与调用代码的硬约束决定。
ALLOWED_CONFIG_KEYS = frozenset(
    {
        "temperature",
        "max_tokens",
        "top_p",
        "n",
        "stop",
        "seed",
        "presence_penalty",
        "frequency_penalty",
        "logit_bias",
        "logprobs",
        "top_logprobs",
        "reasoning_effort",
        "verbosity",
        "timeout",
        "max_retries",
        "stream_chunk_timeout",
        "default_headers",
        "default_query",
        "extra_body",
        "model_kwargs",
        "tiktoken_model_name",
        "service_tier",
        "disabled_params",
        "include_response_headers",
    }
)


@dataclass(frozen=True, slots=True)
class ModelProfile:
    """一次模型调用所需的全部参数（连接方式 + 默认采样参数 + 用途预算）。

    ``key`` 是配置表里的模型标识（日志、指标、灰度按它归集）；``model`` 才是传给厂商的
    模型名。配置表没命中时两者相同（旧口径下槽位填的就是模型名）。

    ``params`` 是**构造期**参数（``init_chat_model(**kwargs)``），不含单次调用的覆盖——
    后者由 ``LlmClient.chat(..., overrides=...)`` 合并，避免调用点各改一份档案。
    """

    purpose: str
    key: str
    model: str
    provider: str = "openai"
    base_url: str = ""
    api_key: str = ""
    params: Mapping[str, Any] = field(default_factory=dict)
    timeout: float | None = None

    @property
    def label(self) -> str:
        """指标与日志里的模型快照：优先模型标识，回落模型名。"""
        return self.key or self.model


def purpose_slot(purpose: str) -> str:
    """用途槽位（env）：填模型标识，配置表里没有同名行时当作模型名直传。"""
    if purpose == PURPOSE_KB_ANSWER:
        return settings.LLM_MODEL_KB_ANSWER or settings.LLM_MODEL
    if purpose == PURPOSE_GRADE_JUDGE:
        return settings.LLM_MODEL_GRADE_JUDGE or settings.LLM_MODEL
    if purpose == PURPOSE_PARSE_FAQ:
        return settings.LLM_MODEL_PARSE_FAQ or settings.LLM_MODEL
    raise ValueError(f"未知的模型用途：{purpose}（可选：{', '.join(PURPOSES)}）")


def purpose_timeout(purpose: str) -> float | None:
    """用途的整体预算（秒）：由调用点 ``asyncio.wait_for`` 使用，与模型连接超时无关。"""
    if purpose == PURPOSE_KB_ANSWER:
        return settings.KB_LLM_TIMEOUT
    if purpose == PURPOSE_GRADE_JUDGE:
        return settings.KB_GRADE_JUDGE_TIMEOUT
    if purpose == PURPOSE_PARSE_FAQ:
        return settings.PARSE_FAQ_TIMEOUT
    raise ValueError(f"未知的模型用途：{purpose}（可选：{', '.join(PURPOSES)}）")


def scene_params(purpose: str) -> Mapping[str, Any]:
    """用途场景的硬性生成要求：写在代码里，**覆盖**配置表的模型级默认值。

    FAQ 抽取是这里唯一的条目：同一份文档要抽出一致的结果，低温是场景要求而不是模型的
    默认脾气，所以它不该被某个模型行改掉。
    """
    if purpose == PURPOSE_PARSE_FAQ:
        return {"temperature": 0.1}
    return {}


def env_params(purpose: str) -> Mapping[str, Any]:
    """兜底参数（来自 env 的各用途配置项）：配置表没写的键才用它。

    这是**逐键**兜底而不是整份替换：老部署普遍只改了配置表里的连接方式，没抄采样参数，
    整份替换会让问答默默从 ``temperature=0.2`` 变成网关默认值。
    """
    if purpose == PURPOSE_KB_ANSWER:
        return {"temperature": settings.KB_ANSWER_TEMPERATURE, "max_tokens": settings.KB_ANSWER_MAX_TOKENS}
    return {}


def filter_config(raw: Mapping[str, Any] | None, *, source: str = "") -> dict[str, Any]:
    """按白名单过滤 ``default_config_json``：不认识的键丢弃并告警，不打挂调用。

    丢弃而不是报错：配置表是运维手填的，写错一个键名不应该让整条问答链路 503；
    但必须留痕，否则「我明明配了 top_k 为什么不生效」会变成一场排查。
    """
    if not raw:
        return {}
    allowed: dict[str, Any] = {}
    for key, value in raw.items():
        if key in ALLOWED_CONFIG_KEYS:
            allowed[key] = value
        else:
            logger.warning("模型配置里出现不可覆盖的键，已丢弃：key={} source={} 值={!r}", key, source or "-", value)
    return allowed


def merge_params(purpose: str, model_defaults: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """合并构造期参数：env 兜底 → 配置表默认 → 用途场景（单次调用的覆盖由调用方追加）。

    逐键覆盖：表里写了就盖掉 env 同名键，没写就沿用 env——这样既有部署只要不填
    ``default_config_json`` 就不会因为接入配置表而改变生成效果。
    """
    merged = dict(env_params(purpose))
    merged.update(filter_config(model_defaults))
    merged.update(scene_params(purpose))
    return merged


def build_profile(
    purpose: str,
    *,
    key: str,
    model: str,
    provider: str = "",
    base_url: str = "",
    api_key: str = "",
    model_defaults: Mapping[str, Any] | None = None,
) -> ModelProfile:
    """由配置表行构造档案：用途只决定预算，模型侧全部来自行数据。"""
    return ModelProfile(
        purpose=purpose,
        key=key,
        model=model or key,
        provider=provider or "openai",
        base_url=base_url,
        api_key=api_key,
        params=merge_params(purpose, model_defaults),
        timeout=purpose_timeout(purpose),
    )


def profile_for(purpose: str, *, model: str | None = None) -> ModelProfile:
    """纯 env 档案（不查表）：配置表关闭、不可用或未命中时走这里。

    ``model`` 显式给了就优先（GSI 入参指定模型等场景）；温度与上限取 env 的各用途配置项。
    """
    key = model or purpose_slot(purpose)
    params = dict(env_params(purpose))
    params.update(scene_params(purpose))
    return ModelProfile(
        purpose=purpose,
        key=key,
        model=key,
        provider="openai",
        params=params,
        timeout=purpose_timeout(purpose),
    )


__all__ = [
    "ALLOWED_CONFIG_KEYS",
    "PURPOSES",
    "PURPOSE_GRADE_JUDGE",
    "PURPOSE_KB_ANSWER",
    "PURPOSE_PARSE_FAQ",
    "ModelProfile",
    "build_profile",
    "env_params",
    "filter_config",
    "merge_params",
    "profile_for",
    "purpose_slot",
    "purpose_timeout",
    "scene_params",
]
