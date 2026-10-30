"""知识库运营统计：文档状态、分片规模、引用排行与上传趋势。

口径说明：
1. 文档与空间统计一律排除已删除行（``is_deleted``）；
2. 分片数走 ``chunk.vector_store.count_chunks``：向量能力未开启时按 0 返回，
   不伪造数字（否则总览页看起来「有分片、实际检索不到」）；
3. 趋势按「日期 + 计数」在数据库侧分组，缺失日期在应用侧补 0，前端无需处理空洞。
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import CustomException

from ..chunk.vector_store import count_chunks, vector_store_ready
from ..document.model import DocumentStatus, IntegrationKbDocumentModel
from ..space.model import IntegrationKbSpaceModel
from .schema import (
    DocumentStatusStatSchema,
    HotDocumentStatSchema,
    SpaceStatSchema,
    StatsOverviewOutSchema,
    TrendPointSchema,
)

# 状态文案（与前端标签保持同一套口径）
_STATUS_LABELS: dict[int, str] = {
    int(DocumentStatus.PENDING): "待解析",
    int(DocumentStatus.PARSING): "解析中",
    int(DocumentStatus.DONE): "已完成",
    int(DocumentStatus.FAILED): "失败",
}


class IntegrationKbStatsService:
    """知识库统计服务（只读）"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def overview(self) -> StatsOverviewOutSchema:
        """总览指标：空间数 / 文档状态分布 / 分片数 / 原件总大小。"""
        doc_stmt = select(
            func.count(IntegrationKbDocumentModel.id),
            func.coalesce(
                func.sum(case((IntegrationKbDocumentModel.status == int(DocumentStatus.DONE), 1), else_=0)), 0
            ),
            func.coalesce(
                func.sum(case((IntegrationKbDocumentModel.status == int(DocumentStatus.PARSING), 1), else_=0)), 0
            ),
            func.coalesce(
                func.sum(case((IntegrationKbDocumentModel.status == int(DocumentStatus.FAILED), 1), else_=0)), 0
            ),
            func.coalesce(
                func.sum(case((IntegrationKbDocumentModel.status == int(DocumentStatus.PENDING), 1), else_=0)), 0
            ),
            func.coalesce(func.sum(case((IntegrationKbDocumentModel.is_enabled.is_(True), 1), else_=0)), 0),
            func.coalesce(func.sum(IntegrationKbDocumentModel.file_size), 0),
        ).where(IntegrationKbDocumentModel.is_deleted.is_(False))
        row = (await self.db.execute(doc_stmt)).one()

        space_count = (
            await self.db.execute(
                select(func.count()).select_from(IntegrationKbSpaceModel).where(IntegrationKbSpaceModel.is_deleted.is_(False))
            )
        ).scalar() or 0

        return StatsOverviewOutSchema(
            space_count=int(space_count),
            document_count=int(row[0] or 0),
            parsed_count=int(row[1] or 0),
            parsing_count=int(row[2] or 0),
            failed_count=int(row[3] or 0),
            pending_count=int(row[4] or 0),
            enabled_count=int(row[5] or 0),
            chunk_count=await self._chunk_count(),
            total_size_mb=round(int(row[6] or 0) / (1024 * 1024), 2),
        )

    async def document_status(self) -> list[DocumentStatusStatSchema]:
        """文档状态分布（固定输出四种状态，缺失补 0，便于前端画同构图表）。"""
        stmt = (
            select(IntegrationKbDocumentModel.status, func.count(IntegrationKbDocumentModel.id))
            .where(IntegrationKbDocumentModel.is_deleted.is_(False))
            .group_by(IntegrationKbDocumentModel.status)
        )
        counts = {int(status): int(total) for status, total in (await self.db.execute(stmt)).all()}
        return [
            DocumentStatusStatSchema(status=status, label=label, count=counts.get(status, 0))
            for status, label in _STATUS_LABELS.items()
        ]

    async def hot_documents(self, limit: int = 10) -> list[HotDocumentStatSchema]:
        """文档引用排行（问答命中引用时累加 ref_count）。"""
        stmt = (
            select(
                IntegrationKbDocumentModel.id,
                IntegrationKbDocumentModel.space_id,
                IntegrationKbDocumentModel.title,
                IntegrationKbDocumentModel.ref_count,
                IntegrationKbDocumentModel.chunk_count,
            )
            .where(IntegrationKbDocumentModel.is_deleted.is_(False))
            .order_by(IntegrationKbDocumentModel.ref_count.desc(), IntegrationKbDocumentModel.id.desc())
            .limit(max(1, limit))
        )
        return [
            HotDocumentStatSchema(
                doc_id=int(row[0]), space_id=int(row[1]), title=row[2] or "", ref_count=int(row[3] or 0), chunk_count=int(row[4] or 0)
            )
            for row in (await self.db.execute(stmt)).all()
        ]

    async def space_ranking(self, limit: int = 10) -> list[SpaceStatSchema]:
        """空间知识量排行（文档数 + 分片数）。"""
        stmt = (
            select(
                IntegrationKbDocumentModel.space_id,
                func.count(IntegrationKbDocumentModel.id),
                func.coalesce(func.sum(IntegrationKbDocumentModel.chunk_count), 0),
                IntegrationKbSpaceModel.name,
            )
            .join(IntegrationKbSpaceModel, IntegrationKbSpaceModel.id == IntegrationKbDocumentModel.space_id, isouter=True)
            .where(IntegrationKbDocumentModel.is_deleted.is_(False))
            .group_by(IntegrationKbDocumentModel.space_id, IntegrationKbSpaceModel.name)
            .order_by(func.count(IntegrationKbDocumentModel.id).desc())
            .limit(max(1, limit))
        )
        return [
            SpaceStatSchema(
                space_id=int(row[0]),
                space_name=row[3] or f"空间 {row[0]}",
                doc_count=int(row[1] or 0),
                chunk_count=int(row[2] or 0),
            )
            for row in (await self.db.execute(stmt)).all()
        ]

    async def upload_trend(self, days: int = 7) -> list[TrendPointSchema]:
        """近 N 天上传趋势（按创建日期分组，空洞补 0）。"""
        span = max(1, days)
        start = (datetime.now(UTC) - timedelta(days=span - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
        day_col = func.date(IntegrationKbDocumentModel.created_time)
        stmt = (
            select(day_col, func.count(IntegrationKbDocumentModel.id))
            .where(IntegrationKbDocumentModel.is_deleted.is_(False), IntegrationKbDocumentModel.created_time >= start)
            .group_by(day_col)
        )
        counts = {str(day): int(total) for day, total in (await self.db.execute(stmt)).all()}
        points: list[TrendPointSchema] = []
        for offset in range(span):
            day = (start + timedelta(days=offset)).date().isoformat()
            points.append(TrendPointSchema(date=day, count=counts.get(day, 0)))
        return points

    async def _chunk_count(self) -> int:
        """分片总数（向量能力不可用时按 0 处理）。"""
        if not vector_store_ready():
            return 0
        try:
            return await count_chunks()
        except CustomException:
            return 0
