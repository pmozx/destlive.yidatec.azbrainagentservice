"""GSI 知识生效快照只读服务。"""

from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_schema import PageResultSchema
from app.utils.common_util import search_to_dict

from .crud import SbpKmDocRecordCRUD
from .schema import (
    SbpKmDocRecordDetailOutSchema,
    SbpKmDocRecordOutSchema,
    SbpKmDocRecordQueryParam,
)


class SbpKmDocRecordService:
    """``sbp_km_doc_record`` 查询服务，不包含任何写逻辑。"""

    def __init__(self, db: AsyncSession) -> None:
        self.crud = SbpKmDocRecordCRUD(db)

    async def page(
        self,
        page_no: int,
        page_size: int,
        search: SbpKmDocRecordQueryParam | None = None,
        order_by: list[dict[str, str]] | None = None,
    ) -> PageResultSchema[SbpKmDocRecordOutSchema]:
        """分页查询生效快照（列表不返回 snapshot_json 大字段）。

        默认按 ``created_at`` 倒序：页面要回答的是「最近一次送审是谁、送给哪个团队、什么时候」，
        最新一条排最前才看得清脉络；索引 ``idx_km_doc_record_doc (doc_id, created_at DESC)``
        正好覆盖这个顺序。
        """
        return await self.crud.page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=order_by or [{"created_at": "desc"}],
            search=search_to_dict(search),
            out_schema=SbpKmDocRecordOutSchema,
        )

    async def detail(self, id: str) -> SbpKmDocRecordDetailOutSchema:
        """查询生效快照详情（含 snapshot_json）。"""
        return cast(
            SbpKmDocRecordDetailOutSchema,
            await self.crud.get_or_404(
                id=id,
                msg="GSI 知识生效快照不存在",
                out_schema=SbpKmDocRecordDetailOutSchema,
            ),
        )
