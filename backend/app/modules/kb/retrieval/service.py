"""知识检索服务（#07）：关键词通道（T4.1）+ 自然语言通道（T4.2）。

口径（与前序任务的验收断言对齐）：

1. **授权先于召回**：先由 ``modules_gsi`` 按签名 ``user_id`` 算出可检索发布单元集合，
   空集合直接返回空结果（``no_evidence=true``）；绝不做「全库召回再过滤」；
2. **两通道各管一段**：关键词通道抓字面命中（编号、专有名词），向量通道抓语义相近
   （换个说法也能召回）；融合只用 RRF 的 rank，不比原始分——关键词覆盖率与余弦相似度
   本来就没有可比量纲；
3. **``score`` 的取法**：精排可用时用重排序相关度（绝对口径，归一在 ``core.chat.rerank``
   完成，此处只能看到 0–1），不可用时用 RRF 相对分（按 top1 归一）。两者都落在 0–1；
4. **``snippet`` 就是本次命中的已授权切片正文**（FR-RAG-09）：整段原样返回，不截断、不折叠、
   不跨来源拼接——契约 #04 的示例给的就是切片全文，调用方要拿它核对原文；
5. **不静默降级**：授权数据不可用 → 503；自然语言通道的向量化不可用 → 503（不能拿
   「只有字面命中」冒充语义检索结果）；精排不可用只影响排序，回落融合序并留日志。
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.exc import SQLAlchemyError

from app.config.setting import settings
from app.core.chat import rerank_candidates, to_candidates
from app.core.clients import get_embedding_client
from app.core.database import async_session_factory
from app.core.exceptions import CustomException
from app.core.logger import logger
from app.modules_gsi.km.doc_publication.service import SbpKmDocPublicationService

from .crud import ChunkCandidate, KbSearchCRUD
from .schema import SearchHit, SearchQuery, SearchResult

# 关键词切分：空白与中英文标点（含句号）；连续分隔符不产生空 token
_SPLIT_PATTERN = re.compile("[\u3000\\s,，、。;；:：!！?？/|()（）\\[\\]【】\"“”'‘’]+")
# 单次检索最多保留多少个词：防止超长自然语言把 SQL 的 AND 条件撑爆
_MAX_KEYWORDS = 8
# 候选放大倍数：SQL 只做命中过滤，最终顺序由应用层打分/融合决定，需要多取一些
_CANDIDATE_FACTOR = 5
_CANDIDATE_FLOOR = 50


class RetrievalAuthorizationUnavailable(Exception):
    """权限数据不可用：契约要求 503``AUTHORIZATION_UNAVAILABLE``，且不得降级全库检索。"""


class RetrievalEmbeddingUnavailable(Exception):
    """自然语言通道的向量化不可用：契约按依赖故障转 503，不退化成纯关键词检索。"""


#: 检索通道取值（契约 #07 与管理端调试台同一枚举）
MODE_KEYWORD = "keyword"
MODE_NATURAL_LANGUAGE = "natural_language"


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


def _to_hits(
    scored: list[tuple[float, ChunkCandidate]],
    *,
    limit: int,
) -> tuple[SearchHit, ...]:
    """(分数, 候选) → 契约 hits：两条通道共用同一投影，顺序由调用方给定。

    ``snippet`` 直接给切片正文全文：检索结果就是调用方的证据，摘一段定长窗口会让人无法
    核对原文（契约 #04 的示例值也是整段正文）。展示层要折叠/截断由前端自己做。
    """
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
            snippet=candidate.content,
            score=score,
            page_from=candidate.page_from,
            page_to=candidate.page_to,
        )
        for score, candidate in scored[:limit]
    )


def _keyword_scored(candidates: list[ChunkCandidate], keywords: tuple[str, ...]) -> list[tuple[float, ChunkCandidate]]:
    """关键词打分 → 稳定排序（同分按发布单元 / 切片序号）。"""
    scored = [(_score(candidate.content, candidate.heading, keywords), candidate) for candidate in candidates]
    scored.sort(key=lambda item: (-item[0], item[1].publication_id, item[1].seq))
    return scored


def _rrf_fuse(*channels: Sequence[ChunkCandidate]) -> list[tuple[float, ChunkCandidate]]:
    """RRF 融合：单通道得分 ``1/(k+rank)``，跨通道相加，最后按 top1 归一到 0–1。

    只吃排名不吃分值：关键词覆盖率与余弦相似度不能直接加权。同一切片被两条通道同时
    命中时得分自然更高——这正是 RRF 想要的「多路共识」。归一后 top1 恒为 1.0，
    与关键词模式的「模式内相对分」口径一致，不对调用方承诺绝对置信度。
    """
    k = max(1, settings.SEARCH_RRF_K)
    merged: dict[tuple[str, str, int], tuple[float, ChunkCandidate]] = {}
    for channel in channels:
        for rank, candidate in enumerate(channel, start=1):
            key = (candidate.publication_id, candidate.record_id, candidate.seq)
            fused_score, first = merged.get(key, (0.0, candidate))
            merged[key] = (fused_score + 1.0 / (k + rank), first)
    ordered = sorted(merged.values(), key=lambda item: (-item[0], item[1].publication_id, item[1].seq))
    if not ordered:
        return []
    top = ordered[0][0]
    return [(round(score / top, 4), candidate) for score, candidate in ordered]


@dataclass(frozen=True, slots=True)
class _RerankText:
    """精排入参适配：复用 ``core.chat.rerank`` 的 ``RankedHit`` 协议（标题 / 正文 / 分）。

    ``section_title`` 放「文档标题 + 切片小标题」而不是只放小标题：实测中文 query ×
    英文正文时，不喂文档标题的精排 logit 全是负数（-0.44 / -2.62 / -3.81），喂了标题
    立刻回到 3.64 / 3.15 / 1.56——文档标题是最强的跨语言锚点，也是文档级主题的唯一载体，
    而 ``heading`` 往往只是「Abstract / 2. Methods」这类英文小标题。
    """

    section_title: str | None
    content: str
    score: float
    candidate: ChunkCandidate


def _rerank_input(candidate: ChunkCandidate, score: float) -> _RerankText:
    """构造单条精排入参：标题不截断，正文截到 ``SEARCH_RERANK_DOC_CHARS``。

    截断只影响送给交叉编码器的原文量，不影响返回给调用方的 ``snippet``（始终是完整切片
    正文）；实测 800 字符正文比 500 字符多花约 25% 时间，而排序结果不变（关键句大多在
    切片开头）。
    """
    title = "\n".join(part for part in (candidate.title, candidate.heading) if part)
    content = candidate.content[: max(100, settings.SEARCH_RERANK_DOC_CHARS)]
    return _RerankText(section_title=title or None, content=content, score=score, candidate=candidate)


async def _rerank(
    query: str,
    fused: list[tuple[float, ChunkCandidate]],
    *,
    limit: int,
) -> list[tuple[float, ChunkCandidate]]:
    """对融合结果精排；服务未接入、超时或调用失败时原样回落（RRF 序 + 相对分）。

    闸门分两层，全部在 ``core.chat.rerank`` 归一后的 0–1 空间比较：

    1. **绝对下限** ``KB_RERANK_MIN_SCORE``：最相关的一条都不够格 → 库里确实没依据，
       返回空列表让契约如实给出 ``no_evidence=true``，不拿低分命中凑数；
    2. **相对比例** ``KB_RERANK_RELATIVE_RATIO``：裁掉与 top1 差距过大的长尾，避免把
       相关度 2% 的切片当命中同时给给前端。

    两层写成 ``max`` 而不是“逐条过绝对闸门”：搜索场景下绝对阈值只该回答「有没有依据」，
    条数由相对比例与 ``limit`` 决定；否则一个偏细的绝对阈值会把整页结果扫成空。

    窗口（``SEARCH_RERANK_WINDOW``）同时也是本通道的返回条数上限：没被精排打分的尾部
    候选不补进结果——精排分与融合分不同源，混在同一个 ``score`` 字段里会给前端不可比的数值。
    """
    window = fused[: max(1, settings.SEARCH_RERANK_WINDOW)]
    texts = [_rerank_input(candidate, score) for score, candidate in window]
    ranked, used = await rerank_candidates(
        query, to_candidates(texts), len(texts), timeout=settings.SEARCH_RERANK_TIMEOUT
    )
    if not used:
        # core.chat.rerank 已经记过降级日志，这里只回落到融合序
        return window[:limit]

    top = ranked[0].final_score
    floor = max(settings.KB_RERANK_MIN_SCORE, top * settings.KB_RERANK_RELATIVE_RATIO)
    accepted = [(item.final_score, item.hit.candidate) for item in ranked if item.final_score >= floor]
    logger.info(
        "精排完成：窗口={} top1={} 闸门={} 保留={}",
        len(window),
        top,
        round(floor, 4),
        len(accepted),
    )
    return accepted[:limit]


def _keyword_window(limit: int) -> int:
    """关键词候选上限：SQL 只做命中过滤，排序在应用层，需要多取一些。"""
    return min(settings.SEARCH_KEYWORD_CANDIDATE_MAX, max(limit * _CANDIDATE_FACTOR, _CANDIDATE_FLOOR))


def _vector_window(limit: int) -> int:
    """向量候选上限：HNSW 粗排窗口，至少覆盖 ``KB_RETRIEVE_TOP_K`` 的精排规模。"""
    return min(settings.SEARCH_VECTOR_CANDIDATE_MAX, max(limit * _CANDIDATE_FACTOR, settings.KB_RETRIEVE_TOP_K))


class KbRetrievalService:
    """知识检索服务：关键词通道与自然语言（向量 / RRF / 精排）通道共用同一授权前置。"""

    async def search(self, mode: str, data: SearchQuery) -> SearchResult:
        """按 ``mode`` 选通道：契约 #07 与管理端调试台共用一个入口。

        分发写在这里而不是各出口各写一遍：两边对 ``mode`` 的处理一旦分叉，就会出现
        「同一句 query 在契约走向量、在调试台走字面命中」——这恰恰是调试台最用来排查的事。
        未知取值按关键词通道处理（与 ``Literal`` 枚举外的宽容口径一致，不凭拼写报错）。
        """
        if mode == MODE_NATURAL_LANGUAGE:
            return await self.natural_language_search(data)
        return await self.keyword_search(data)

    async def keyword_search(self, data: SearchQuery) -> SearchResult:
        """按关键词检索当前生效快照，返回授权范围内的 chunk 级命中。"""
        keywords = _tokenize(data.query)
        if not keywords:
            return SearchResult(hits=())

        authorized = await self.authorized_publication_ids(data.user_id)
        if not authorized:
            logger.info("检索授权：user_id={} 无受众团队或无可检索发布单元，返回空结果", data.user_id)
            return SearchResult(hits=())

        async with async_session_factory() as session:
            candidates = await KbSearchCRUD(session).keyword_candidates(
                keywords=keywords,
                tenant_id=data.tenant_id,
                publication_ids=authorized,
                limit=_keyword_window(data.limit),
            )

        hits = _to_hits(_keyword_scored(candidates, keywords), limit=data.limit)
        logger.info(
            "关键词检索完成：user_id={} 授权发布单元={} 候选={} 命中={}",
            data.user_id,
            len(authorized),
            len(candidates),
            len(hits),
        )
        return SearchResult(hits=hits, authorized_publication_count=len(authorized))

    async def natural_language_search(self, data: SearchQuery) -> SearchResult:
        """自然语言检索：授权 → 向量召回 + 关键词召回 → RRF → 精排 → 截断。"""
        keywords = _tokenize(data.query)

        authorized = await self.authorized_publication_ids(data.user_id)
        if not authorized:
            logger.info("自然语言检索授权：user_id={} 无可检索发布单元，返回空结果", data.user_id)
            return SearchResult(hits=())

        # 向量化是外部调用（bge-m3 单条也要上百毫秒），放在事务之外，不占连接池
        embedding = await self._embed_query(data.query)

        window = _vector_window(data.limit)
        async with async_session_factory() as session:
            crud = KbSearchCRUD(session)
            vector_candidates = await crud.vector_candidates(
                embedding=embedding,
                tenant_id=data.tenant_id,
                publication_ids=authorized,
                limit=window,
                min_score=settings.KB_RETRIEVE_MIN_SCORE,
            )
            keyword_candidates = await crud.keyword_candidates(
                keywords=keywords,
                tenant_id=data.tenant_id,
                publication_ids=authorized,
                limit=window,
            )

        fused = _rrf_fuse(vector_candidates, keyword_candidates)
        hits = _to_hits(await _rerank(data.query, fused, limit=data.limit), limit=data.limit)
        logger.info(
            "自然语言检索完成：user_id={} 授权发布单元={} 向量候选={} 关键词候选={} 融合={} 命中={}",
            data.user_id,
            len(authorized),
            len(vector_candidates),
            len(keyword_candidates),
            len(fused),
            len(hits),
        )
        return SearchResult(hits=hits, authorized_publication_count=len(authorized))

    async def authorized_publication_ids(self, user_id: str) -> set[str]:
        """按 ``user_id`` 算可检索发布单元（JOIN 在 ``modules_gsi`` 内实现，此处只调用）。

        三个调用方共用同一前置：契约 #07 检索、管理端调试台、P5 会话与问答
        （``#14`` 检索前、``#10`` 历史消息可见性判定）。公开而不是各自复制一份，
        是因为一旦哪条链路自己算授权，就会出现「检索看得见、历史看不见」的不一致。
        """
        async with async_session_factory() as session:
            try:
                return await SbpKmDocPublicationService(session).list_authorized_publication_ids(user_id)
            except SQLAlchemyError as exc:
                # 共享表缺失 / 连接失败：契约要求 503，绝不放开成全库检索
                logger.error("检索授权查询失败（GSI 共享表不可用）: {}", exc)
                raise RetrievalAuthorizationUnavailable("权限数据暂不可用，请稍后重试。") from exc

    @staticmethod
    async def _embed_query(query: str) -> list[float]:
        """查询向量：必须与写入用同一模型（bge-m3），维度不符按依赖故障处理。

        任何失败都抛 :class:`RetrievalEmbeddingUnavailable`（契约转 503），不静默退化成
        关键词检索——调用方按自然语言提问，只给字面命中会掩盖「语义通道没生效」。
        """
        try:
            vector = await get_embedding_client().embed_query(query)
            if len(vector) != settings.VECTOR_DIM:
                raise ValueError(f"向量维度 {len(vector)} 与 VECTOR_DIM={settings.VECTOR_DIM} 不一致")
            return [float(value) for value in vector]
        except Exception as exc:  # noqa: BLE001 依赖故障统一收敛成 503
            detail = exc.msg if isinstance(exc, CustomException) else str(exc)
            logger.warning("检索向量化失败：{}", detail)
            raise RetrievalEmbeddingUnavailable("向量化依赖暂不可用，请稍后重试。") from exc
