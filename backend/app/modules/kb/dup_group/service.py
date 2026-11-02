"""重复分组治理服务：列表 / 详情 / 标记处理（#16–#20 物化结果的管理端入口）。"""

from fastapi import status as http_status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_schema import PageResultSchema
from app.core.exceptions import CustomException
from app.utils.common_util import search_to_dict

from .crud import KbDupGroupCRUD
from .schema import KbDupGroupDetailOutSchema, KbDupGroupOutSchema, KbDupGroupQueryParam, KbDupGroupStatusSchema


class KbDupGroupService:
    """重复分组服务。"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.crud = KbDupGroupCRUD(db)

    async def page(
        self,
        *,
        page_no: int,
        page_size: int,
        search: KbDupGroupQueryParam | None = None,
        order_by: list[dict[str, str]] | None = None,
    ) -> PageResultSchema[KbDupGroupOutSchema]:
        """分页查询：默认按创建时间倒序（最近检测结果在最前）。"""
        result = await self.crud.page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=order_by or [{"created_time": "desc"}],
            search=search_to_dict(search),
        )
        items = [
            KbDupGroupOutSchema(
                id=row.id,
                uuid=row.uuid,
                created_time=row.created_time,
                updated_time=row.updated_time,
                resource_type=row.resource_type,
                status=row.status,
                similarity=float(row.similarity) if row.similarity is not None else None,
                member_count=len(row.members or []),
            )
            for row in result.items
        ]
        return PageResultSchema(
            page_no=result.page_no,
            page_size=result.page_size,
            total=result.total,
            has_next=result.has_next,
            items=items,
        )

    async def detail(self, group_id: int) -> KbDupGroupDetailOutSchema:
        """分组详情（含成员与合并预览）。"""
        row = await self.crud.get_or_404(id=group_id, msg="重复分组不存在")
        return KbDupGroupDetailOutSchema(
            id=row.id,
            uuid=row.uuid,
            created_time=row.created_time,
            updated_time=row.updated_time,
            resource_type=row.resource_type,
            status=row.status,
            similarity=float(row.similarity) if row.similarity is not None else None,
            member_count=len(row.members or []),
            scope=row.scope,
            members=row.members or [],
            merged_preview=row.merged_preview,
        )

    async def update_status(self, group_id: int, data: KbDupGroupStatusSchema) -> KbDupGroupDetailOutSchema:
        """标记合并/忽略：只改治理状态，不动成员内容。"""
        row = await self.crud.get_or_404(id=group_id, msg="重复分组不存在", out_schema=None)
        if row.status != "active":
            raise CustomException(
                msg=f"分组已处于 {row.status} 状态，不能重复处理",
                status_code=http_status.HTTP_409_CONFLICT,
            )
        await self.crud.set(ids=[group_id], status=data.status)
        return await self.detail(group_id)
