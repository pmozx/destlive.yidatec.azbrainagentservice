"""智能体能力（规范 14.1.1 / 14.1.2 的代码落地点）。

对外只有四件事：用途标识、模型档案、构建入口、正文流消费。业务侧**不得**直接
``from langchain.agents import create_agent``，也**不得**使用已被取代的入口——
``langgraph.prebuilt.create_react_agent``、``AgentExecutor``、``LLMChain``（及旧 chain）。

两档口径（与团队规范 L24 一致）：

- **直出档**：一次检索 → 一次生成，没有分支决策 → 用 ``LlmClient.chat`` / ``stream_chat_events``；
- **决策档**：模型要决定下一步（工具调用、检索改写、二次追问）→ 必须用本包的 ``build_agent``，
  禁止自己维护 ``messages.append(...)`` 的 ReAct 循环。

``ModelProfile`` 承载「用途 → 模型 + 连接方式 + 默认参数 + 超时」，让灰度、成本归集与故障
切换能按用途单独执行；它不决定调用方式，两档都用同一份档案。

模型侧配置以配置表 ``sbp_ai_system_model_config`` 为准（:func:`load_profile`，基座自维护、页面可改），表里查不到或不可用时
回落 env（:func:`profile_for`）；因此业务侧只调 ``load_profile``，不要再自己读 ``LLM_MODEL_*``。
"""

from .builder import AgentStreamChunk, build_agent, stream_agent_text
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
    "AgentStreamChunk",
    "ModelConfigRegistry",
    "ModelConfigRow",
    "ModelProfile",
    "build_agent",
    "build_profile",
    "get_model_config_registry",
    "invalidate_model_config_cache",
    "load_profile",
    "profile_for",
    "reset_model_config_registry",
    "stream_agent_text",
]
