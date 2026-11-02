"""GSI 用户有效性只读服务。"""

from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_schema import PageResultSchema
from app.utils.common_util import search_to_dict

from .crud import SbpSysUserCRUD
from .schema import SbpSysUserOutSchema, SbpSysUserQueryParam


class SbpSysUserService:
    """``sbp_sys_user`` 查询服务，不包含任何写逻辑。"""

    def __init__(self, db: AsyncSession) -> None:
        self.crud = SbpSysUserCRUD(db)

    async def page(
        self,
        page_no: int,
        page_size: int,
        search: SbpSysUserQueryParam | None = None,
        order_by: list[dict[str, str]] | None = None,
    ) -> PageResultSchema[SbpSysUserOutSchema]:
        """分页查询用户有效性。"""
        return await self.crud.page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=order_by or [{"id": "asc"}],
            search=search_to_dict(search),
            out_schema=SbpSysUserOutSchema,
        )

    async def detail(self, id: int) -> SbpSysUserOutSchema:
        """查询用户有效性详情。"""
        return cast(
            SbpSysUserOutSchema,
            await self.crud.get_or_404(
                id=id, msg="GSI 用户不存在", out_schema=SbpSysUserOutSchema
            ),
        )
