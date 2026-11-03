"""关键词检索服务（#07 keyword · T4.1）。

口径（与前序任务的验收断言对齐）：

1. **授权先于召回**：先由 ``modules_gsi`` 按签名 ``user_id`` 算出可检索发布单元集合，
   空集合直接返回空结果（``no_evidence=true``）；绝不做「全库召回再过滤」；
2. **``score`` 是模式内相对分**（0–1）：由关键词覆盖率、出现密度、标题命中构成。
   它与向量通道的余弦分不同量纲——T4.2 做 RRF 只能跨通道用 rank，不能直接比值；
3. **``snippet`` 只来自本次命中的已授权切片正文**（FR-RAG-09），不跨来源拼接；
4. 授权数据不可用时抛 ``RetrievalAuthorizationUnavailable``，契约层转 503，
   **不降级为不校验授权的检索**。
"""

import re

from sqlalchemy.exc import SQLAlchemyError

from app.config.setting import settings
from app.core.database import async_session_factory
from app.core.logger import logger
from app.modules_gsi.km.doc_publication.service import SbpKmDocPublicationService

from .crud import KeywordCandidate, KbSearchCRUD
from .schema import KeywordQuery, SearchHit, SearchResult

# 关键词切分：空白与中英文标点（含句号）；连续分隔符不产生空 token
_SPLIT_PATTERN = re.compile("[\u3000\\s,，、。;；:：!！?？/|()（）\\[\\]【】\"“”'‘’]+")
# 单次检索最多保留多少个词：防止超长自然语言把 SQL 的 AND 条件撑爆
_MAX_KEYWORDS = 8
# 候选放大倍数：SQL 只做命中过滤，最终顺序由应用层打分决定，需要多取一些
_CANDIDATE_FACTOR = 5
_CANDIDATE_FLOOR = 50


class RetrievalAuthorizationUnavailable(Exception):
    """权限数据不可用：契约要求 503``AUTHORIZATION_UNAVAILABLE``，且不得降级全库检索。"""


def _tokenize(query: str) -> tuple[str, ...]:
    """切词去重保序并限流。

    中文没有分词器，按空白/标点切后整段做子串匹配：``"如何申请 VPN？"`` →
    ``("如何申请", "VPN")``。词内不再二次切片，避免把「申请」这类短词拆成噪声。
    """
    tokens: list[str] = []
    for raw in _SPLIT_PATTERN.split(query.strip()):
        token = raw.strip()
        if token and token not in tokens:
            tokens.append(token)
    return tuple(tokens[:_MAX_KEYWORDS])


def _score(content: str, heading: str | None, keywords: tuple[str, ...]) -> float:
    """关键词相对分 = 覆盖率 0.7 + 密度 0.2 + 标题命中 0.1，钳在 0–1。"""
    content_lower = content.lower()
    heading_lower = (heading or "").lower()
    coverage = sum(1 for keyword in keywords if keyword.lower() in content_lower) / len(keywords)
    occurrences = sum(min(content_lower.count(keyword.lower()), 3) for keyword in keywords)
    density = min(1.0, occurrences / (3 * len(keywords)))
    heading_hits = sum(1 for keyword in keywords if keyword.lower() in heading_lower) / len(keywords)
    return round(min(1.0, 0.7 * coverage + 0.2 * density + 0.1 * heading_hits), 4)


def _snippet(content: str, keywords: tuple[str, ...]) -> str:
    """截取命中点附近的定长摘要（折叠空白），只取本切片正文。"""
    text = " ".join(content.split())
    limit = max(20, settings.SEARCH_SNIPPET_LENGTH)
    lowered = text.lower()
    positions = [lowered.find(keyword.lower()) for keyword in keywords]
    hits = [position for position in positions if position >= 0]
    position = min(hits) if hits else 0
    # 命中点放在窗口前 1/4 处，前后都留上下文
    start = max(0, position - limit // 4)
    window = text[start : start + limit]
    prefix = "…" if start > 0 else ""
    suffix = "…" if start + limit < len(text) else ""
    return f"{prefix}{window}{suffix}"


def _ranked_hits(
    candidates: list[KeywordCandidate],
    *,
    keywords: tuple[str, ...],
    limit: int,
) -> tuple[SearchHit, ...]:
    """打分 → 稳定排序（同分按发布单元 / 切片序号）→ 截断到契约 limit。"""
    scored = [(_score(candidate.content, candidate.heading, keywords), candidate) for candidate in candidates]
    scored.sort(key=lambda item: (-item[0], item[1].publication_id, item[1].seq))
    return tuple(
        SearchHit(
            resource_type=candidate.resource_type,
            resource_id=candidate.resource_id,
            publication_id=candidate.publication_id,
            record_id=candidate.record_id,
            version_id=candidate.version_id,
            revision=candidate.revision,
            knowledge_no=candidate.knowledge_no,
            chunk_id=candidate.chunk_id,
            title=candidate.title,
            snippet=_snippet(candidate.content, keywords),
            score=score,
            page_from=candidate.page_from,
            page_to=candidate.page_to,
        )
        for score, candidate in scored[:limit]
    )


class KbRetrievalService:
    """知识检索服务：本期只有关键词通道，T4.2 在同一入口追加自然语言通道。"""

    async def keyword_search(self, data: KeywordQuery) -> SearchResult:
        """按关键词检索当前生效快照，返回授权范围内的 chunk 级命中。"""
        keywords = _tokenize(data.query)
        if not keywords:
            return SearchResult(hits=())

        async with async_session_factory() as session:
            try:
                authorized = await SbpKmDocPublicationService(session).list_authorized_publication_ids(data.user_id)
            except SQLAlchemyError as exc:
                # 共享表缺失 / 连接失败：契约要求 503，绝不放开成全库检索
                logger.error("检索授权查询失败（GSI 共享表不可用）: {}", exc)
                raise RetrievalAuthorizationUnavailable("权限数据暂不可用，请稍后重试。") from exc

            if not authorized:
                logger.info("检索授权：user_id={} 无受众团队或无可检索发布单元，返回空结果", data.user_id)
                return SearchResult(hits=())

            candidate_limit = min(
                settings.SEARCH_KEYWORD_CANDIDATE_MAX,
                max(data.limit * _CANDIDATE_FACTOR, _CANDIDATE_FLOOR),
            )
            candidates = await KbSearchCRUD(session).keyword_candidates(
                keywords=keywords,
                tenant_id=data.tenant_id,
                publication_ids=authorized,
                limit=candidate_limit,
            )

        hits = _ranked_hits(candidates, keywords=keywords, limit=data.limit)
        logger.info(
            "关键词检索完成：user_id={} 授权发布单元={} 候选={} 命中={}",
            data.user_id,
            len(authorized),
            len(candidates),
            len(hits),
        )
        return SearchResult(hits=hits)
