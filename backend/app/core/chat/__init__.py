"""AI 问答核心能力：重排序、置信判定、流式输出消费。

设计约定：
- ``rerank`` 通过 ``RankedHit`` 协议解耦业务命中结构，不 import 任何业务模型；
- ``streaming`` 只负责超时/断连判定与流关闭，不写业务表；
- 提示词、引用、检索等业务语义留在业务域，core 只提供可复用的稳定机制。
"""

from .rerank import (
    Candidate,
    GradeResult,
    RankedHit,
    grade,
    rerank_candidates,
    to_candidates,
)
from .streaming import (
    StreamInterruptedError,
    StreamTimeoutError,
    close_stream,
    iter_stream,
)

__all__ = [
    "Candidate",
    "GradeResult",
    "RankedHit",
    "StreamInterruptedError",
    "StreamTimeoutError",
    "close_stream",
    "grade",
    "iter_stream",
    "rerank_candidates",
    "to_candidates",
]
