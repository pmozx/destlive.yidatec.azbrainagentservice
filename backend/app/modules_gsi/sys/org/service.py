"""GSI 组织架构只读服务。"""

from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_schema import PageResultSchema
from app.utils.common_util import search_to_dict

from .crud import SbpSysOrgCRUD
from .schema import SbpSysOrgOutSchema, SbpSysOrgQueryParam


class SbpSysOrgService:
    """``sbp_sys_org`` 查询服务，不包含任何写逻辑。"""

    def __init__(self, db: AsyncSession) -> None:
        self.crud = SbpSysOrgCRUD(db)

    async def page(
        self,
        page_no: int,
        page_size: int,
        search: SbpSysOrgQueryParam | None = None,
        order_by: list[dict[str, str]] | None = None,
    ) -> PageResultSchema[SbpSysOrgOutSchema]:
        """分页查询组织架构。"""
        return await self.crud.page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=order_by or [{"id": "asc"}],
            search=search_to_dict(search),
            out_schema=SbpSysOrgOutSchema,
        )

    async def detail(self, id: int) -> SbpSysOrgOutSchema:
        """查询组织详情。"""
        return cast(
            SbpSysOrgOutSchema,
            await self.crud.get_or_404(
                id=id, msg="GSI 组织不存在", out_schema=SbpSysOrgOutSchema
            ),
        )
