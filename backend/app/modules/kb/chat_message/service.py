"""会话消息服务：管理端只读维护（按会话看历史、看单条引用留痕）。

消息由 ``/v1/chat/completions`` 写入，管理端**不提供编辑**：对话内容一旦生成就是审计
证据，能改就等于能改历史；需要下架时改状态而不是改正文。
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_schema import PageResultSchema
from app.utils.common_util import search_to_dict

from .crud import KbChatMessageCRUD
from .schema import KbChatMessageOutSchema, KbChatMessageQueryParam


class KbChatMessageService:
    """会话消息服务（只读）。"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.crud = KbChatMessageCRUD(db)

    async def page(
        self,
        *,
        page_no: int,
        page_size: int,
        search: KbChatMessageQueryParam | None = None,
        order_by: list[dict[str, str]] | None = None,
    ) -> PageResultSchema[KbChatMessageOutSchema]:
        """分页查询：默认按主键升序，保持与对话发生顺序一致。"""
        return await self.crud.page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=order_by or [{"id": "asc"}],
            search=search_to_dict(search),
            out_schema=KbChatMessageOutSchema,
        )

    async def detail(self, message_id: str) -> KbChatMessageOutSchema:
        """单条详情（``message_id`` 即表 ``uuid``），不存在 404。"""
        return await self.crud.get_or_404(
            uuid=message_id,
            msg="会话消息不存在",
            out_schema=KbChatMessageOutSchema,
        )
