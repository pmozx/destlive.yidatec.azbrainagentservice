"""检索数据层：关键词与向量两条通道，共用「授权 + 生效指针 + 状态」下推过滤。

设计要点：

1. **过滤全部下推 SQL**：契约 #07 明确禁止「先全库召回再过滤授权」，因此
   ``publication_id IN (授权集合)`` 与租户、状态一起写进 WHERE；
2. **只认当前生效快照**：``chunk.record_id = publication.current_record_id``——旧版本切片
   仍留在库里（可回退），但绝不能被检索召回；
3. **两条通道共用一套投影与过滤**（:func:`_candidate_select` / :func:`_candidate_filters`），
   只在「文本 AND 命中」与「余弦距离排序」上分叉，避免两份 JOIN 各改一半；
4. **向量参数按文本 CAST**：pgvector 的 ORM 类型会给参数挂 bind processor，而 asyncpg
   不需要驱动侧编解码（与 ``core.rag.vector_store`` 同口径），显式 ``CAST(:p AS VECTOR)``
   最稳，也不受连接池类型注册影响；
5. **关键词打分与摘要截取放在应用层**（``service.py``）：不依赖 PG 专有的 ``similarity()``，
   也让两条通道共用同一套响应组装；
6. 多关键词按 **AND** 命中（每个词都要出现在正文或标题里），召回精准且可解释；
   单关键词时退化为一次 ``ILIKE``。
"""

from collections.abc import Sequence
from dataclasses import dataclass

from pgvector.sqlalchemy import Vector
from sqlalchemy import Text, and_, bindparam, cast, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from app.config.setting import settings

from ..chunk.model import KbChunkModel
from ..publication.model import KbPublicationModel
from ..revision.model import KbRevisionModel

PUBLICATION_ACTIVE = "active"
CHUNK_ONLINE = "online"
# ILIKE 的转义符：关键词里的 % / _ 必须当字面量，否则会变成通配符
LIKE_ESCAPE = "\\"


@dataclass(frozen=True)
class ChunkCandidate:
    """候选命中（两条通道共用，未定稿分数）：字段与契约 hits[] 一一对应。"""

    publication_id: str
    resource_type: str
    resource_id: str
    record_id: str
    version_id: str
    knowledge_no: str
    revision: int
    title: str
    chunk_id: str
    seq: int
    heading: str | None
    content: str
    page_from: int | None
    page_to: int | None
    #: 向量通道的余弦相似度（已钳到 0–1）；关键词通道为 None，分值由应用层打分
    vector_score: float | None = None


def like_pattern(keyword: str) -> str:
    """``%关键词%``，并转义 LIKE 通配符（用户输入里的 % 与 _ 是字面量）。"""
    escaped = keyword.replace(LIKE_ESCAPE, LIKE_ESCAPE * 2).replace("%", f"{LIKE_ESCAPE}%").replace("_", f"{LIKE_ESCAPE}_")
    return f"%{escaped}%"


def vector_literal(values: Sequence[float]) -> str:
    """pgvector 文本字面量 ``[0.1,0.2,…]``：参数按文本下发，由 PG 侧 CAST 成 vector。"""
    return "[" + ",".join(f"{float(value):.8f}" for value in values) + "]"


def _candidate_select() -> Select:
    """候选投影：chunk ⋈ publication ⋈ revision，一次 JOIN 取齐契约要求的回显字段。"""
    return (
        select(
            KbChunkModel.publication_id,
            KbPublicationModel.resource_type,
            KbPublicationModel.resource_id,
            KbChunkModel.record_id,
            KbRevisionModel.version_id,
            KbRevisionModel.knowledge_no,
            KbRevisionModel.revision,
            KbPublicationModel.title,
            KbChunkModel.chunk_id,
            KbChunkModel.seq,
            KbChunkModel.heading,
            KbChunkModel.content,
            KbChunkModel.page_from,
            KbChunkModel.page_to,
        )
        .join(
            KbPublicationModel,
            KbPublicationModel.publication_id == KbChunkModel.publication_id,
        )
        .join(
            KbRevisionModel,
            and_(
                KbRevisionModel.publication_id == KbChunkModel.publication_id,
                KbRevisionModel.record_id == KbChunkModel.record_id,
            ),
        )
    )


def _candidate_filters(*, tenant_id: str, publication_ids: set[str]) -> tuple:
    """两条通道共用的硬过滤：租户 + 发布单元生效 + 快照可检索 + 切片在线 + 授权集合。"""
    return (
        KbPublicationModel.tenant_id == tenant_id,
        KbPublicationModel.status == PUBLICATION_ACTIVE,
        KbPublicationModel.current_record_id.isnot(None),
        KbRevisionModel.searchable.is_(True),
        KbChunkModel.status == CHUNK_ONLINE,
        KbChunkModel.publication_id.in_(publication_ids),
        # 生效指针：同一发布单元的历史快照不得召回
        KbChunkModel.record_id == KbPublicationModel.current_record_id,
    )


def _to_candidate(row, *, vector_score: float | None = None) -> ChunkCandidate:
    """查询行 → 领域候选（``chunk_id`` 为空时按 ``record_id:seq`` 回显，与 03.2 口径一致）。"""
    return ChunkCandidate(
        publication_id=row.publication_id,
        resource_type=row.resource_type,
        resource_id=row.resource_id or "",
        record_id=row.record_id,
        version_id=row.version_id or "",
        knowledge_no=row.knowledge_no or "",
        revision=row.revision,
        title=row.title or "",
        chunk_id=row.chunk_id or f"{row.record_id}:{row.seq}",
        seq=row.seq,
        heading=row.heading,
        content=row.content,
        page_from=row.page_from,
        page_to=row.page_to,
        vector_score=vector_score,
    )


class KbSearchCRUD:
    """检索只读数据访问（只提供查询，没有任何写方法）。"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def keyword_candidates(
        self,
        *,
        keywords: tuple[str, ...],
        tenant_id: str,
        publication_ids: set[str],
        limit: int,
    ) -> list[ChunkCandidate]:
        """取关键词候选（已按授权/生效指针/租户过滤，未排序打分）。

        ``limit`` 是候选上限而不是最终条数：应用层还要按命中质量重排后截断，
        取值由 ``service.py`` 按契约 ``limit`` 放大得到。
        """
        if not keywords or not publication_ids or limit <= 0:
            return []

        # 每个关键词都必须命中：正文或标题出现即可（OR 关系在词内，AND 关系跨词）
        text_clause = and_(
            *(
                or_(
                    KbChunkModel.content.ilike(like_pattern(keyword), escape=LIKE_ESCAPE),
                    KbChunkModel.heading.ilike(like_pattern(keyword), escape=LIKE_ESCAPE),
                )
                for keyword in keywords
            )
        )

        rows = await self.db.execute(
            _candidate_select()
            .where(
                *_candidate_filters(tenant_id=tenant_id, publication_ids=publication_ids),
                text_clause,
            )
            .order_by(KbChunkModel.publication_id, KbChunkModel.seq)
            .limit(limit)
        )
        return [_to_candidate(row) for row in rows.all()]

    async def vector_candidates(
        self,
        *,
        embedding: Sequence[float],
        tenant_id: str,
        publication_ids: set[str],
        limit: int,
        min_score: float,
    ) -> list[ChunkCandidate]:
        """取向量候选：按余弦距离升序，``min_score`` 在 SQL 侧就挡掉不相关切片。

        距离阈值一并下推（``distance <= 1 - min_score``），HNSW 仍按距离做近似最近邻；
        没有向量的切片（历史数据或向量化失败的）直接排除，不参与排序。
        """
        if not embedding or not publication_ids or limit <= 0:
            return []

        # 文本参数 + 显式 CAST：避免 pgvector 类型给 bind 挂 processor，asyncpg 侧最稳
        query_vector = cast(
            bindparam("query_embedding", vector_literal(embedding), type_=Text),
            Vector(settings.VECTOR_DIM),
        )
        distance = KbChunkModel.embedding.cosine_distance(query_vector)

        rows = await self.db.execute(
            _candidate_select()
            .add_columns((1 - distance).label("vector_score"))
            .where(
                *_candidate_filters(tenant_id=tenant_id, publication_ids=publication_ids),
                KbChunkModel.embedding.isnot(None),
                distance <= 1 - min_score,
            )
            .order_by(distance, KbChunkModel.publication_id, KbChunkModel.seq)
            .limit(limit)
        )
        return [
            # 余弦距离理论取值 0–2，相似度钳到契约的 0–1
            _to_candidate(row, vector_score=min(1.0, max(0.0, float(row.vector_score))))
            for row in rows.all()
        ]
