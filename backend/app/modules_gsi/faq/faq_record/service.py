"""GSI FAQ 审批快照只读服务。"""

from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_schema import PageResultSchema
from app.utils.common_util import search_to_dict

from .crud import SbpFaqRecordCRUD
from .schema import (
    SbpFaqRecordDetailOutSchema,
    SbpFaqRecordOutSchema,
    SbpFaqRecordQueryParam,
)


class SbpFaqRecordService:
    """``sbp_faq_record`` 查询服务，不包含任何写逻辑。"""

    def __init__(self, db: AsyncSession) -> None:
        self.crud = SbpFaqRecordCRUD(db)

    async def page(
        self,
        page_no: int,
        page_size: int,
        search: SbpFaqRecordQueryParam | None = None,
        order_by: list[dict[str, str]] | None = None,
    ) -> PageResultSchema[SbpFaqRecordOutSchema]:
        """分页查询审批快照（列表不返回 snapshot_json 大字段）。"""
        return await self.crud.page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=order_by or [{"id": "asc"}],
            search=search_to_dict(search),
            out_schema=SbpFaqRecordOutSchema,
        )

    async def detail(self, id: str) -> SbpFaqRecordDetailOutSchema:
        """查询审批快照详情（含 snapshot_json）。"""
        return cast(
            SbpFaqRecordDetailOutSchema,
            await self.crud.get_or_404(
                id=id,
                msg="GSI FAQ 审批快照不存在",
                out_schema=SbpFaqRecordDetailOutSchema,
            ),
        )
