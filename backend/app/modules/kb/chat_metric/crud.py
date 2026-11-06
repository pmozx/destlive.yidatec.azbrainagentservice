"""问答指标数据层：按会话取明细、按会话条件聚合。

指标与消息是两套事实源，本层只读、且不 join 业务口径（正文、引用、依赖都不在这张表里）。

唯一一处 JOIN 是汇总：概览卡要与「会话记录」列表**同一筛选口径**，而指标表没有标题 /
状态 / 用户主键，只能在会话表上过滤。反过来做（先查会话 id 再 ``IN``）会让参数个数随
会话量线性膨胀，翻到全量时这条 SQL 就没法用了。
"""

from collections.abc import Sequence

from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.core.base_crud import CRUDBase

from ..chat_session.model import KbChatSessionModel
from .model import KbChatTurnMetricModel
from .schema import TurnSummary


class KbChatTurnMetricCRUD(CRUDBase[KbChatTurnMetricModel, BaseModel, BaseModel]):
    """问答指标数据层（只读）。"""

    def __init__(self, db: AsyncSession) -> None:
        super().__init__(model=KbChatTurnMetricModel, db=db)

    async def summary(self, filters: Sequence[ColumnElement]) -> TurnSummary:
        """按会话条件聚合一组轮次；``filters`` 作用在会话表上。

        ``avg`` 用 SQL 的聚合而不是先取行再平均：一页会话可能对应上千轮，
        把行取回到 Python 只为算三个均值没有意义。``first_token_ms`` 在非流式轮是
        ``NULL``，SQL 的 ``avg`` 自动跳过它——这正是想要的「只对流式轮算首字」。
        """
        metric = KbChatTurnMetricModel
        result = await self.db.execute(
            select(
                func.count().label("turns"),
                func.coalesce(func.sum(case((metric.no_evidence.is_(True), 1), else_=0)), 0).label(
                    "no_evidence_turns"
                ),
                func.coalesce(func.sum(case((metric.error_code.isnot(None), 1), else_=0)), 0).label("failed_turns"),
                func.avg(metric.total_ms).label("avg_total_ms"),
                func.avg(metric.first_token_ms).label("avg_first_token_ms"),
                func.coalesce(func.sum(metric.tokens_in), 0).label("tokens_in"),
                func.coalesce(func.sum(metric.tokens_out), 0).label("tokens_out"),
            )
            .select_from(metric)
            .join(KbChatSessionModel, KbChatSessionModel.uuid == metric.session_id)
            .where(*filters)
        )
        row = result.one()
        return TurnSummary(
            turns=int(row.turns or 0),
            no_evidence_turns=int(row.no_evidence_turns or 0),
            failed_turns=int(row.failed_turns or 0),
            avg_total_ms=_as_int(row.avg_total_ms),
            avg_first_token_ms=_as_int(row.avg_first_token_ms),
            tokens_in=int(row.tokens_in or 0),
            tokens_out=int(row.tokens_out or 0),
        )


def _as_int(value: object) -> int | None:
    """``avg`` 在 PostgreSQL 上返回 ``numeric``（Decimal），统一转成毫秒整数。"""
    if value is None:
        return None
    return int(round(float(value)))  # type: ignore[arg-type]
