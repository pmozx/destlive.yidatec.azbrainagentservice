"""AI 问答核心能力：重排序、置信判定、证据二判、引用后处理、流式输出消费。

设计约定：
- ``rerank`` / ``judge`` 只依赖协议与文本，不 import 任何业务模型；
- ``streaming`` 只负责超时/断连判定与流关闭，不写业务表；
- 提示词、检索、会话落库等业务语义留在业务域，core 只提供可复用的稳定机制；
- 引用快照由业务域的证据对象直接投影，core 不再维护第二套 ``build_cites`` 口径。
"""

from .cite import sanitize_citations
from .judge import SufficiencyVerdict, judge_sufficiency
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
    "GradeResult",
    "HeartbeatResult",
    "HeartbeatTick",
    "RankedHit",
    "StreamInterruptedError",
    "StreamTimeoutError",
    "SufficiencyVerdict",
    "TICK",
    "close_stream",
    "grade",
    "iter_stream",
    "iter_with_heartbeat",
    "judge_sufficiency",
    "normalize_rerank_score",
    "rerank_candidates",
    "sanitize_citations",
    "to_candidates",
]
