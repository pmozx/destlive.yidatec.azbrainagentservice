"""兼容层：重排序与置信判定已迁至 ``app.core.chat.rerank``，此处只做转发。

新代码请直接引用 ``app.core.chat.rerank``，不要再依赖本模块。
"""

from app.core.chat.rerank import (
    Candidate,
    GradeResult,
    RankedHit,
    grade,
    rerank_candidates,
    to_candidates,
)

__all__ = ["Candidate", "GradeResult", "RankedHit", "grade", "rerank_candidates", "to_candidates"]
