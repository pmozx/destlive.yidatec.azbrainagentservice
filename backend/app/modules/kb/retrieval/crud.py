"""关键词检索数据层：一条 SQL 完成「授权 + 生效指针 + 文本命中」过滤。

设计要点：

1. **过滤全部下推 SQL**：契约 #07 明确禁止「先全库召回再过滤授权」，因此
   ``publication_id IN (授权集合)`` 与租户、状态一起写进 WHERE；
2. **只认当前生效快照**：``chunk.record_id = publication.current_record_id``——旧版本切片
   仍留在库里（可回退），但绝不能被检索召回；
3. **打分与摘要截取放在应用层**（``service.py``）：不依赖 PG 专有的 ``similarity()``，
   便于后续向量通道复用同一套响应组装，也让 SQL 保持可读；
4. 多关键词按 **AND** 命中（每个词都要出现在正文或标题里），召回精准且可解释；
   单关键词时退化为一次 ``ILIKE``。
"""

from dataclasses import dataclass

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..chunk.model import KbChunkModel
from ..publication.model import KbPublicationModel
from ..revision.model import KbRevisionModel

PUBLICATION_ACTIVE = "active"
CHUNK_ONLINE = "online"
# ILIKE 的转义符：关键词里的 % / _ 必须当字面量，否则会变成通配符
LIKE_ESCAPE = "\\"


@dataclass(frozen=True)
class KeywordCandidate:
    """候选命中（未打分）：检索投影需要的全部列，字段与契约 hits[] 对应。"""

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


def like_pattern(keyword: str) -> str:
    """``%关键词%``，并转义 LIKE 通配符（用户输入里的 % 与 _ 是字面量）。"""
    escaped = keyword.replace(LIKE_ESCAPE, LIKE_ESCAPE * 2).replace("%", f"{LIKE_ESCAPE}%").replace("_", f"{LIKE_ESCAPE}_")
    return f"%{escaped}%"


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
    ) -> list[KeywordCandidate]:
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
            .where(
                KbPublicationModel.tenant_id == tenant_id,
                KbPublicationModel.status == PUBLICATION_ACTIVE,
                KbPublicationModel.current_record_id.isnot(None),
                KbRevisionModel.searchable.is_(True),
                KbChunkModel.status == CHUNK_ONLINE,
                KbChunkModel.publication_id.in_(publication_ids),
                # 生效指针：同一发布单元的历史快照不得召回
                KbChunkModel.record_id == KbPublicationModel.current_record_id,
                text_clause,
            )
            .order_by(KbChunkModel.publication_id, KbChunkModel.seq)
            .limit(limit)
        )
        return [
            KeywordCandidate(
                publication_id=row.publication_id,
                resource_type=row.resource_type,
                resource_id=row.resource_id or "",
                record_id=row.record_id,
                version_id=row.version_id or "",
                knowledge_no=row.knowledge_no or "",
                revision=row.revision,
                title=row.title or "",
                # 存量行的 chunk_id 可能为 NULL：与 03.2 口径一致，按 record_id:seq 回显
                chunk_id=row.chunk_id or f"{row.record_id}:{row.seq}",
                seq=row.seq,
                heading=row.heading,
                content=row.content,
                page_from=row.page_from,
                page_to=row.page_to,
            )
            for row in rows.all()
        ]
