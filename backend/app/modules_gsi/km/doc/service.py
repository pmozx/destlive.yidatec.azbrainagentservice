"""GSI 知识文档只读服务。"""

from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_schema import PageResultSchema
from app.utils.common_util import search_to_dict

from .crud import SbpKmDocCRUD
from .schema import SbpKmDocOutSchema, SbpKmDocQueryParam


class SbpKmDocService:
    """``sbp_km_doc`` 查询服务，不包含任何写逻辑。"""

    def __init__(self, db: AsyncSession) -> None:
        self.crud = SbpKmDocCRUD(db)

    async def page(
        self,
        page_no: int,
        page_size: int,
        search: SbpKmDocQueryParam | None = None,
        order_by: list[dict[str, str]] | None = None,
    ) -> PageResultSchema[SbpKmDocOutSchema]:
        """分页查询知识文档元数据。"""
        return await self.crud.page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=order_by or [{"id": "asc"}],
            search=search_to_dict(search),
            out_schema=SbpKmDocOutSchema,
        )

    async def detail(self, id: str) -> SbpKmDocOutSchema:
        """查询知识文档元数据详情。"""
        return cast(
            SbpKmDocOutSchema,
            await self.crud.get_or_404(
                id=id, msg="GSI 知识文档不存在", out_schema=SbpKmDocOutSchema
            ),
        )
