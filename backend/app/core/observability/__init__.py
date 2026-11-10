"""AI 观测：LangSmith 追踪的进程级开关与 run 命名。

设计约定：
- 追踪只由配置（``AI_TRACE_*``）决定开关与脱敏，业务域不各自读环境变量；
- 本包只提供「建 run / 补 metadata / 取 trace id」这三件事，不感知问答业务字段；
- 若将来引入图编排，节点名复用 ``RUN_*``，trace 树与指标口径不断层。
"""

from .tracing import (
    RUN_GENERATE,
    RUN_RETRIEVE,
    RUN_TURN,
    configure_tracing,
    current_trace_id,
    llm_config,
    trace_metadata,
    traced,
    tracing_enabled,
    user_pseudonym,
)

__all__ = [
    "RUN_GENERATE",
    "RUN_RETRIEVE",
    "RUN_TURN",
    "configure_tracing",
    "current_trace_id",
    "llm_config",
    "trace_metadata",
    "traced",
    "tracing_enabled",
    "user_pseudonym",
]
