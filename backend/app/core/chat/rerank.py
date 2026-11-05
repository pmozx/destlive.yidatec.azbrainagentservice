"""重排序与置信判定（Rerank → TopN → 三闸）。

分数口径（归一化只在这一层做，上游一律只看 0–1）：
1. ``RerankClient`` 只负责传输，返回服务端原始分——llama.cpp ``--rerank`` 是交叉编码器
   **logit**（可正可负），Jina/Cohere 同构服务是 0–1 概率；
2. :func:`normalize_rerank_score` 把它压到 0–1，因此 ``Candidate.rerank_score`` 与
   ``final_score`` **恒为 0–1 相关度**；
3. 三条闸门全部在 0–1 空间比较。相对闸门是「乘比例」，对负数会反向
   （-0.44 × 0.6 = -0.26 > -0.44，最相关的一条反而被裁），所以归一必须先于任何闸门。

三条独立闸门，任一不满足即拒答：
1. 条数闸：有效块数 ≥ ``KB_GRADE_MIN_CHUNKS``；
2. 绝对分闸：精排可用时用 ``KB_RERANK_MIN_SCORE``，否则用 ``KB_RETRIEVE_MIN_SCORE``；
3. 相对分闸：只保留不低于 ``top1 × KB_RERANK_RELATIVE_RATIO`` 的块，裁掉长尾噪声。

> 两种分数量纲不同（精排相关度 / 余弦相似度），阈值**不得互相复用**。

本模块通过 :class:`RankedHit` 协议解耦业务命中结构：调用方只需提供
``section_title`` / ``content`` / ``score`` 三个属性，无需引入任何业务模型。
"""

import math
from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar, runtime_checkable

from app.config.setting import settings
from app.core.clients import get_rerank_client
from app.core.logger import logger


@runtime_checkable
class RankedHit(Protocol):
    """可参与重排的命中项：标题、正文与召回分（向量相似度）。"""

    section_title: str | None
    content: str
    score: float


HitT = TypeVar("HitT", bound=RankedHit)


@dataclass(slots=True)
class Candidate(Generic[HitT]):
    """一个候选命中：召回分 + （可选）精排分，按需取用较高可信度的那个。

    两个分数都是 0–1：``vector_score`` 是召回分（余弦 / RRF 相对分），
    ``rerank_score`` 是已归一的跨编码器相关度。
    """

    hit: HitT
    vector_score: float
    rerank_score: float | None = None

    @property
    def final_score(self) -> float:
        """判定用的分数（0–1）：有精排分就用精排分，否则回落到召回分。"""
        return self.vector_score if self.rerank_score is None else self.rerank_score


def to_candidates(hits: list[HitT]) -> list[Candidate[HitT]]:
    """知识命中 → 候选列表（保持召回顺序即得分降序）。"""
    return [Candidate(hit=hit, vector_score=hit.score) for hit in hits]


def normalize_rerank_score(raw: float) -> float:
    """精排原始分 → 0–1 相关度（上游只看这个数，不感知服务端口径）。

    llama.cpp ``/v1/rerank`` 返回 logit：实测中文 query × 英文正文可以是负数
    （-0.44 / -2.62 / -3.81），而同一批切片带上中文文档标题后是 3.64 / 3.15 / 1.56。
    直接锤到 [0,1] 会把正的 logit 压成清一色 1.0；直接用 logit 做阈值又会把整批命中
    丢掉，两头的后果都是错的，所以统一 sigmoid（单调，排序不变）。
    ``RERANK_SCORE_IS_LOGIT=false``（服务已返回概率）时按原值使用。
    """
    if not settings.RERANK_SCORE_IS_LOGIT:
        return round(min(1.0, max(0.0, raw)), 4)
    bounded = max(-60.0, min(60.0, raw))  # 防 exp 溢出；±60 已足够表达「几乎全信 / 几乎不信」
    return round(1.0 / (1.0 + math.exp(-bounded)), 4)


async def rerank_candidates(
    query: str,
    candidates: list[Candidate[HitT]],
    top_n: int,
    *,
    timeout: float | None = None,
) -> tuple[list[Candidate[HitT]], bool]:
    """调用精排服务重排（失败降级为召回序，不阻断问答/检索）。

    ``timeout`` 缺省沿用 ``RERANK_TIMEOUT``；在线检索链路建议传更短的尾段预算，
    宁可快速降级，也不让调用方在超时上白等。

    返回 ``(重排结果, 是否实际使用了精排)``：第二个值决定置信闸门用哪套阈值。
    """
    if not candidates:
        return candidates[:top_n], False
    client = get_rerank_client()
    if not client.base_url:
        return candidates[:top_n], False

    documents = [
        f"{item.hit.section_title}\n{item.hit.content}" if item.hit.section_title else item.hit.content
        for item in candidates
    ]
    try:
        hits = await client.rerank(query, documents, top_n=min(top_n, len(documents)), timeout=timeout)
    except Exception as exc:  # noqa: BLE001 精排是可选增强：任何失败都降级为召回序
        logger.warning(f"精排调用失败，降级为召回排序：{exc}")
        return candidates[:top_n], False
    if not hits:
        logger.warning("精排返回结果为空，降级为召回排序")
        return candidates[:top_n], False

    reranked: list[Candidate[HitT]] = []
    for item in hits:
        # 下标必须回指入参，否则分数与文本错位；越界下标客户端已丢弃
        candidate = candidates[item.index]
        reranked.append(
            Candidate(
                hit=candidate.hit,
                vector_score=candidate.vector_score,
                rerank_score=normalize_rerank_score(item.score),
            )
        )
    return reranked[:top_n], True


@dataclass(slots=True)
class GradeResult(Generic[HitT]):
    """置信判定结果：通过的候选（得分降序）、实际阈值与拒答原因。"""

    candidates: list[Candidate[HitT]]
    threshold: float
    reranked: bool
    reason: str = ""


def grade(candidates: list[Candidate[HitT]], *, reranked: bool) -> GradeResult[HitT]:
    """置信三闸判定，返回可进入上下文的候选（可能为空 = 应拒答）。

    入参 ``final_score`` 必须是 0–1（:func:`rerank_candidates` 已归一）；相对分闸是
    乘比例比较，传入 logit 会得到反向结果。
    """
    if len(candidates) < max(1, settings.KB_GRADE_MIN_CHUNKS):
        return GradeResult([], 0.0, reranked, "too_few")

    threshold = settings.KB_RERANK_MIN_SCORE if reranked else settings.KB_RETRIEVE_MIN_SCORE
    accepted = [item for item in candidates if item.final_score >= threshold]
    if not accepted:
        return GradeResult([], threshold, reranked, "low_score")

    # 相对分闸：裁掉与 top1 差距过大的长尾块（至少保留 top1）
    floor = accepted[0].final_score * settings.KB_RERANK_RELATIVE_RATIO
    accepted = [item for item in accepted if item.final_score >= floor]
    return GradeResult(accepted, threshold, reranked)
