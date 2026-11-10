"""模型档案与配置解析（规范 14.1.2 / L27 的代码落地点）。

对外只有三件事：用途标识、模型档案、按用途解析档案（另导出 `invalidate_model_config_cache`，供配置写入后清快照）。

``ModelProfile`` 承载「用途 → 模型 + 连接方式 + 默认参数 + 超时」，让灰度、成本归集与故障
切换能按用途单独执行。本包**只回答「用哪个模型、怎么连、参数与预算是多少」**，不决定调用方式：

- 单轮直出（本项目当前唯一形态）：``load_profile(PURPOSE_*)`` → ``LlmClient.chat`` /
  ``stream_chat_events``（没有分支决策，不套 agent 循环）；
- 若将来出现工具决策场景（工具调用、检索改写、二次追问），按团队规范 L24「决策档」引入
  ``create_agent`` 并新增本包的 ``builder`` 模块承载；业务侧**不得**直接
  ``from langchain.agents import create_agent``，也**不得**使用已被取代的入口——
  ``langgraph.prebuilt.create_react_agent``、``AgentExecutor``、``LLMChain``（及旧 chain），
  更不得自己维护 ``messages.append(...)`` 的 ReAct 循环。

模型侧配置以配置表 ``sbp_ai_system_model_config`` 为准（:func:`load_profile`，基座自维护、页面可改），表里查不到或不可用时
回落 env（:func:`profile_for`）；因此业务侧只调 ``load_profile``，不要再自己读 ``LLM_MODEL_*``。
"""

from .profiles import (
    ALLOWED_CONFIG_KEYS,
    PURPOSES,
    PURPOSE_GRADE_JUDGE,
    PURPOSE_KB_ANSWER,
    PURPOSE_PARSE_FAQ,
    ModelProfile,
    build_profile,
    profile_for,
)
from .registry import (
    ModelConfigRegistry,
    ModelConfigRow,
    get_model_config_registry,
    invalidate_model_config_cache,
    load_profile,
    reset_model_config_registry,
)

__all__ = [
    "ALLOWED_CONFIG_KEYS",
    "PURPOSES",
    "PURPOSE_GRADE_JUDGE",
    "PURPOSE_KB_ANSWER",
    "PURPOSE_PARSE_FAQ",
    "ModelConfigRegistry",
    "ModelConfigRow",
    "ModelProfile",
    "build_profile",
    "get_model_config_registry",
    "invalidate_model_config_cache",
    "load_profile",
    "profile_for",
    "reset_model_config_registry",
]
