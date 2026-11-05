"""GSI FAQ 按团队生效关系只读数据访问。"""

from typing import Any

from sqlalchemy import func, select

from app.modules_gsi.base import SbpReadCRUD
from app.modules_gsi.faq.faq_record.model import SbpFaqRecordModel

from .model import SbpFaqPublicationModel


class SbpFaqPublicationCRUD(SbpReadCRUD[SbpFaqPublicationModel]):
    """``sbp_faq_publication`` 读取。"""

    model = SbpFaqPublicationModel

    async def page_with_rules(
        self,
        *,
        offset: int,
        limit: int,
        faq_id: str | None = None,
        team: str | None = None,
        record_id: str | None = None,
        index_status: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """生效关系 + 生效快照规则联表分页。

        联 ``faq_record`` 只为拿 ``snapshot_json`` 作为「publication 规则为空时的回落来源」；
        用 LEFT JOIN：快照被清掉时仍要看到这条发布关系，并把规则判为无效。
        """
        record = SbpFaqRecordModel
        conditions = []
        if faq_id:
            conditions.append(self.model.faq_id == faq_id)
        if team:
            conditions.append(self.model.team == team)
        if record_id:
            conditions.append(self.model.record_id == record_id)
        if index_status:
            conditions.append(self.model.index_status == index_status)
        joins = (
            select(
                self.model.id.label("id"),
                self.model.faq_id.label("faq_id"),
                self.model.team.label("team"),
                self.model.record_id.label("record_id"),
                self.model.access_rules_json.label("access_rules_json"),
                self.model.index_status.label("index_status"),
                self.model.updated_at.label("updated_at"),
                record.snapshot_json.label("snapshot_json"),
            )
            .select_from(self.model)
            .outerjoin(record, record.id == self.model.record_id)
        )
        count_sql = (
            select(func.count())
            .select_from(self.model)
            .outerjoin(record, record.id == self.model.record_id)
            .where(*conditions)
        )
        data_sql = joins.where(*conditions).order_by(
            self.model.updated_at.desc(), self.model.id.asc()
        )
        return await self.fetch_rows(
            data_sql=data_sql, count_sql=count_sql, offset=offset, limit=limit
        )
