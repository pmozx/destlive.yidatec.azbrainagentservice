"""GSI 受众团队只读服务。"""

from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_schema import PageResultSchema
from app.utils.common_util import search_to_dict

from .crud import SbpSysAudienceTeamCRUD
from .schema import SbpSysAudienceTeamOutSchema, SbpSysAudienceTeamQueryParam


class SbpSysAudienceTeamService:
    """``sbp_sys_audience_team`` 查询服务，不包含任何写逻辑。"""

    def __init__(self, db: AsyncSession) -> None:
        self.crud = SbpSysAudienceTeamCRUD(db)

    async def page(
        self,
        page_no: int,
        page_size: int,
        search: SbpSysAudienceTeamQueryParam | None = None,
        order_by: list[dict[str, str]] | None = None,
    ) -> PageResultSchema[SbpSysAudienceTeamOutSchema]:
        """分页查询受众团队。"""
        return await self.crud.page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=order_by or [{"id": "asc"}],
            search=search_to_dict(search),
            out_schema=SbpSysAudienceTeamOutSchema,
        )

    async def detail(self, id: int) -> SbpSysAudienceTeamOutSchema:
        """查询受众团队详情。"""
        return cast(
            SbpSysAudienceTeamOutSchema,
            await self.crud.get_or_404(
                id=id, msg="GSI 受众团队不存在", out_schema=SbpSysAudienceTeamOutSchema
            ),
        )
