"""AI 问答核心能力：重排序、置信判定、引用后处理、流式输出消费。

设计约定：
- ``rerank`` / ``cite`` 通过 ``RankedHit`` / ``CitableHit`` 协议解耦业务命中结构，
  不 import 任何业务模型；
- ``streaming`` 只负责超时/断连判定与流关闭，不写业务表；
- 提示词、检索、会话落库等业务语义留在业务域，core 只提供可复用的稳定机制。
"""

from .cite import (
    CiteItem,
    CitableHit,
    build_cites,
    build_followups,
    build_snippet,
    sanitize_citations,
)
from .rerank import (
    Candidate,
    GradeResult,
    RankedHit,
    grade,
    normalize_rerank_score,
    rerank_candidates,
    to_candidates,
)
from .streaming import (
    TICK,
    HeartbeatResult,
    HeartbeatTick,
    StreamInterruptedError,
    StreamTimeoutError,
    close_stream,
    iter_stream,
    iter_with_heartbeat,
)

__all__ = [
    "Candidate",
    "CiteItem",
    "CitableHit",
    "GradeResult",
    "HeartbeatResult",
    "HeartbeatTick",
    "RankedHit",
    "StreamInterruptedError",
    "StreamTimeoutError",
    "TICK",
    "build_cites",
    "build_followups",
    "build_snippet",
    "close_stream",
    "grade",
    "iter_stream",
    "iter_with_heartbeat",
    "normalize_rerank_score",
    "rerank_candidates",
    "sanitize_citations",
    "to_candidates",
]
