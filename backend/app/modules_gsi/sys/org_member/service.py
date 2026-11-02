"""GSI 组织成员只读服务。"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_schema import PageResultSchema
from app.utils.common_util import search_to_dict

from .crud import SbpSysOrgMemberCRUD
from .schema import SbpSysOrgMemberOutSchema, SbpSysOrgMemberQueryParam


class SbpSysOrgMemberService:
    """``sbp_sys_org_member`` 查询服务，不包含任何写逻辑。"""

    def __init__(self, db: AsyncSession) -> None:
        self.crud = SbpSysOrgMemberCRUD(db)

    async def page(
        self,
        page_no: int,
        page_size: int,
        search: SbpSysOrgMemberQueryParam | None = None,
        order_by: list[dict[str, str]] | None = None,
    ) -> PageResultSchema[SbpSysOrgMemberOutSchema]:
        """分页查询组织成员。"""
        return await self.crud.page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=order_by or [{"org_id": "asc"}, {"user_id": "asc"}],
            search=search_to_dict(search),
            out_schema=SbpSysOrgMemberOutSchema,
        )
