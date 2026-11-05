"""GSI FAQ 主记录只读服务。"""

from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_schema import PageResultSchema
from app.utils.common_util import search_to_dict

from .crud import SbpFaqCRUD
from .schema import SbpFaqOutSchema, SbpFaqQueryParam


class SbpFaqService:
    """``sbp_faq`` 查询服务，不包含任何写逻辑。"""

    def __init__(self, db: AsyncSession) -> None:
        self.crud = SbpFaqCRUD(db)

    async def page(
        self,
        page_no: int,
        page_size: int,
        search: SbpFaqQueryParam | None = None,
        order_by: list[dict[str, str]] | None = None,
    ) -> PageResultSchema[SbpFaqOutSchema]:
        """分页查询 FAQ 主记录。"""
        return await self.crud.page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=order_by or [{"id": "asc"}],
            search=search_to_dict(search),
            out_schema=SbpFaqOutSchema,
        )

    async def detail(self, id: str) -> SbpFaqOutSchema:
        """查询 FAQ 主记录详情。"""
        return cast(
            SbpFaqOutSchema,
            await self.crud.get_or_404(
                id=id, msg="GSI FAQ 不存在", out_schema=SbpFaqOutSchema
            ),
        )
