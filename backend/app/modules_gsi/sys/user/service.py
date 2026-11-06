"""GSI 用户档案只读服务。"""

from fastapi import status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_schema import PageResultSchema
from app.core.exceptions import CustomException
from app.modules_gsi.sys.org.service import SbpSysOrgService
from app.utils.common_util import search_to_dict

from .crud import SbpSysUserCRUD
from .schema import SbpSysUserOutSchema, SbpSysUserQueryParam


class SbpSysUserService:
    """``sbp_sys_user`` 查询服务，不包含任何写逻辑。"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.crud = SbpSysUserCRUD(db)

    async def page(
        self,
        page_no: int,
        page_size: int,
        search: SbpSysUserQueryParam | None = None,
        order_by: list[dict[str, str]] | None = None,
    ) -> PageResultSchema[SbpSysUserOutSchema]:
        """分页查询用户档案（带职级名称、组织/团队归属数）。

        ``org_id`` / ``include_sub`` / ``team_id`` 不是 ``sbp_sys_user`` 的列，
        不能让它们落到 ``_build_conditions`` 里（会被当成未知字段静默忽略、
        变成「看着加了筛选条件却全量返回」），所以先 pop 出来再交给 crud。
        """
        values = search_to_dict(search) or {}
        org_id = values.pop("org_id", None)
        include_sub = bool(values.pop("include_sub", False))
        team_id = values.pop("team_id", None)
        org_ids = None
        if org_id is not None:
            org_ids = (
                await SbpSysOrgService(self.db).subtree_ids(org_id)
                if include_sub
                else [org_id]
            )
        offset = (page_no - 1) * page_size
        rows, total = await self.crud.page_with_relations(
            offset=offset,
            limit=page_size,
            search=values,
            org_ids=org_ids,
            team_id=team_id,
            order_by=order_by,
        )
        return PageResultSchema(
            page_no=page_no,
            page_size=page_size,
            total=total,
            has_next=offset + page_size < total,
            items=[SbpSysUserOutSchema.model_validate(row) for row in rows],
        )

    async def detail(self, id: int) -> SbpSysUserOutSchema:
        """查询用户档案详情（带职级名称）。"""
        row = await self.crud.get_with_rank(id)
        if row is None:
            raise CustomException(
                msg="GSI 用户不存在", status_code=status.HTTP_404_NOT_FOUND
            )
        return SbpSysUserOutSchema.model_validate(row)
