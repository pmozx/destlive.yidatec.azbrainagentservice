"""GSI 职级只读服务。"""

from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_schema import PageResultSchema
from app.utils.common_util import search_to_dict

from .crud import SbpSysRankCRUD
from .schema import SbpSysRankOutSchema, SbpSysRankQueryParam


class SbpSysRankService:
    """``sbp_sys_rank`` 查询服务，不包含任何写逻辑。"""

    def __init__(self, db: AsyncSession) -> None:
        self.crud = SbpSysRankCRUD(db)

    async def page(
        self,
        page_no: int,
        page_size: int,
        search: SbpSysRankQueryParam | None = None,
        order_by: list[dict[str, str]] | None = None,
    ) -> PageResultSchema[SbpSysRankOutSchema]:
        """分页查询职级。"""
        return await self.crud.page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=order_by or [{"sort_no": "asc"}, {"id": "asc"}],
            search=search_to_dict(search),
            out_schema=SbpSysRankOutSchema,
        )

    async def detail(self, id: int) -> SbpSysRankOutSchema:
        """查询职级详情。"""
        return cast(
            SbpSysRankOutSchema,
            await self.crud.get_or_404(
                id=id, msg="GSI 职级不存在", out_schema=SbpSysRankOutSchema
            ),
        )
