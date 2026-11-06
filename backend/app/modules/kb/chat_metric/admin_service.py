"""问答指标管理端服务：单轮明细与会话维度汇总（只读）。

**为什么不把指标并进「会话消息」接口**：消息表是逐条投影给 GSI 的审计证据（正文 + 引用
八元组 + 依赖三元组），指标是可自由加列的观测数据；两者混进同一个响应，任何一侧加字段
都会改动另一侧的契约。页面按 ``assistant_message_id`` 在前端对齐即可——失败轮没有助手
消息，本来就只能作为独立行展示。

**为什么汇总要 JOIN 会话表**：概览卡与列表页必须同一筛选口径（会话 ID / 用户 / 标题 /
状态都只存在于会话表），指标表只有 ``session_id``。JOIN 一次比「先查会话再 IN」稳：
后者参数个数随会话量线性增长。
"""

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.core.base_schema import PageResultSchema

from ..chat_session.model import KbChatSessionModel
from .crud import KbChatTurnMetricCRUD
from .model import KbChatTurnMetricModel
from .schema import (
    KbChatTurnMetricOutSchema,
    KbChatTurnMetricSummaryOutSchema,
    KbChatTurnMetricSummaryQueryParam,
    TurnSummary,
)


class KbChatTurnMetricAdminService:
    """问答指标管理端服务。"""

    def __init__(self, db: AsyncSession) -> None:
        self.crud = KbChatTurnMetricCRUD(db)

    async def page(
        self,
        *,
        page_no: int,
        page_size: int,
        session_id: str,
    ) -> PageResultSchema[KbChatTurnMetricOutSchema]:
        """按会话取单轮明细：``created_time`` 升序，与对话流的阅读顺序一致。

        不用 ``out_schema`` 让 Pydantic 直接吃 ORM 行：字段改名时它会静默取默认值
        （会话列表曾经整列空白），这里统一走 :meth:`to_out` 显式映射。
        """
        result = await self.crud.page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=[{"created_time": "asc"}, {"id": "asc"}],
            search={"session_id": ("eq", session_id)},
        )
        result.items = [self.to_out(row) for row in result.items]
        return result

    async def summary(
        self,
        search: KbChatTurnMetricSummaryQueryParam | None = None,
    ) -> KbChatTurnMetricSummaryOutSchema:
        """按会话维度聚合：返回比率与均值，页面不再各自算一遍。"""
        agg = await self.crud.summary(self._session_filters(search))
        return self.to_summary_out(agg)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 私有
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    @staticmethod
    def _session_filters(search: KbChatTurnMetricSummaryQueryParam | None) -> list[ColumnElement]:
        """会话维度筛选 → 会话表列条件；空筛选表示「全部会话」。

        ``session_id`` / ``title`` 用 ``like`` 与会话列表接口保持一致（同一页面上的两个
        控件如果模糊/精确口径不同，用户会以为其中一个没生效）。
        """
        if search is None:
            return []
        filters: list[ColumnElement] = []
        if search.session_id:
            filters.append(KbChatSessionModel.uuid.like(f"%{search.session_id}%"))
        if search.user_id is not None:
            filters.append(KbChatSessionModel.user_id == search.user_id)
        if search.title:
            filters.append(KbChatSessionModel.title.like(f"%{search.title}%"))
        if search.status:
            filters.append(KbChatSessionModel.status == search.status)
        return filters

    @staticmethod
    def to_out(row: KbChatTurnMetricModel) -> KbChatTurnMetricOutSchema:
        """模型 → 明细响应（``created_time`` 由 ``DateTimeStr`` 统一时区输出）。"""
        return KbChatTurnMetricOutSchema(
            id=row.id,
            created_time=row.created_time,
            session_id=row.session_id,
            assistant_message_id=row.assistant_message_id,
            trace_id=row.trace_id,
            mode=row.mode,
            model=row.model,
            prompt_hash=row.prompt_hash,
            evidence_count=row.evidence_count,
            top_score=row.top_score,
            tokens_in=row.tokens_in,
            tokens_out=row.tokens_out,
            retrieve_ms=row.retrieve_ms,
            first_token_ms=row.first_token_ms,
            generate_ms=row.generate_ms,
            persist_ms=row.persist_ms,
            total_ms=row.total_ms,
            no_evidence=row.no_evidence,
            finish_reason=row.finish_reason,
            invalid_citations=row.invalid_citations,
            error_code=row.error_code,
        )

    @staticmethod
    def to_summary_out(agg: TurnSummary) -> KbChatTurnMetricSummaryOutSchema:
        """聚合结果 → 响应；拒答率在 0 轮时记 0（不产生 NaN）。"""
        rate = agg.no_evidence_turns / agg.turns if agg.turns else 0.0
        return KbChatTurnMetricSummaryOutSchema(
            turns=agg.turns,
            no_evidence_turns=agg.no_evidence_turns,
            no_evidence_rate=round(rate, 4),
            failed_turns=agg.failed_turns,
            avg_total_ms=agg.avg_total_ms,
            avg_first_token_ms=agg.avg_first_token_ms,
            tokens_in=agg.tokens_in,
            tokens_out=agg.tokens_out,
        )
