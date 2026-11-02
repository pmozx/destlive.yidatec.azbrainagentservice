"""GSI 文档按团队生效关系只读服务。"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_schema import PageResultSchema
from app.utils.common_util import search_to_dict

from .crud import SbpKmDocPublicationCRUD
from .schema import SbpKmDocPublicationOutSchema, SbpKmDocPublicationQueryParam


class SbpKmDocPublicationService:
    """``sbp_km_doc_publication`` 查询服务，不包含任何写逻辑。"""

    def __init__(self, db: AsyncSession) -> None:
        self.crud = SbpKmDocPublicationCRUD(db)

    async def page(
        self,
        page_no: int,
        page_size: int,
        search: SbpKmDocPublicationQueryParam | None = None,
        order_by: list[dict[str, str]] | None = None,
    ) -> PageResultSchema[SbpKmDocPublicationOutSchema]:
        """分页查询文档按团队生效关系。"""
        return await self.crud.page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=order_by or [{"updated_at": "desc"}],
            search=search_to_dict(search),
            out_schema=SbpKmDocPublicationOutSchema,
        )
