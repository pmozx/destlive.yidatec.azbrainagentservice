"""GSI 文档按团队生效关系只读服务。"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_schema import PageResultSchema
from app.core.logger import logger
from app.utils.common_util import search_to_dict

from .crud import SbpKmDocPublicationCRUD
from .schema import SbpKmDocPublicationOutSchema, SbpKmDocPublicationQueryParam


class SbpKmDocPublicationService:
    """``sbp_km_doc_publication`` 查询服务，不包含任何写逻辑。"""

    def __init__(self, db: AsyncSession) -> None:
        self.crud = SbpKmDocPublicationCRUD(db)

    async def list_authorized_publication_ids(self, user_id: str) -> set[str]:
        """签名 ``user_id`` → 可检索发布单元集合（#07 检索授权，T4.1）。

        契约把 ``user_id`` 定义成「稳定数字主键的字符串形式」。脏数据或伪造值不做报错、
        也不放行：转不成主键就返回空集——「什么都看不到」是唯一安全的结果；
        GSI 表不可用时异常直接上抛，由契约层转 503 ``AUTHORIZATION_UNAVAILABLE``
        （禁止降级成全库检索）。
        """
        try:
            pk = int(str(user_id).strip())
        except (TypeError, ValueError):
            logger.warning("检索授权：user_id 不是数字主键，按无权限处理: {!r}", user_id)
            return set()
        return await self.crud.list_authorized_publication_ids(user_id=pk)

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
