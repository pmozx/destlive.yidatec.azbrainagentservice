"""问答会话与消息服务：会话列表 / 历史回显 / 重命名 / 删除 / 答案反馈。"""

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_schema import PageResultSchema
from app.core.exceptions import CustomException
from app.utils.common_util import search_to_dict

from .crud import IntegrationChatMessageCRUD, IntegrationChatSessionCRUD
from .model import IntegrationChatMessageModel, IntegrationChatSessionModel
from .schema import MessageCreateSchema
from ..schema import (
    MessageOutSchema,
    SessionCreateSchema,
    SessionDetailOutSchema,
    SessionOutSchema,
    SessionQueryParam,
    SessionUpdateSchema,
)

# 角色：库内以整数落库，对外统一暴露 user / assistant
ROLE_LABEL = {1: "user", 2: "assistant"}


class IntegrationChatSessionService:
    """问答会话服务（无 RBAC：会话列表就是当前使用者的会话）"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.crud = IntegrationChatSessionCRUD(db)
        self.message_crud = IntegrationChatMessageCRUD(db)

    async def page(
        self,
        page_no: int,
        page_size: int,
        search: SessionQueryParam | None = None,
        order_by: list[dict[str, str]] | None = None,
    ) -> PageResultSchema[SessionOutSchema]:
        """分页查询会话（按最近消息时间倒序，空会话回落到创建时间）。"""
        result = await self.crud.page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=order_by or [{"last_message_time": "desc"}, {"id": "desc"}],
            search=search_to_dict(search),
        )
        return PageResultSchema[SessionOutSchema](
            page_no=result.page_no,
            page_size=result.page_size,
            total=result.total,
            has_next=result.has_next,
            items=[self._to_out(item) for item in result.items],
        )

    async def create(self, data: SessionCreateSchema) -> SessionOutSchema:
        """创建一个空会话（前端「新对话」按钮可直接建会话，也可由首问隐式创建）。"""
        obj = await self.crud.create(
            data={
                "title": data.title,
                "scope_type": data.scope_type,
                "space_ids": list(data.space_ids) if data.space_ids else None,
                "message_count": 0,
            }
        )
        return self._to_out(obj)

    async def detail(self, id: int) -> SessionDetailOutSchema:
        """会话详情：会话信息 + 消息明细（时间升序，供前端还原历史对话）。"""
        session = await self.crud.get_or_404(id=id, msg="问答会话不存在")
        messages = await self.message_crud.get_by_session(id)
        return SessionDetailOutSchema(
            session=self._to_out(session),
            messages=[self._to_message_out(item) for item in messages],
        )

    async def update(self, id: int, data: SessionUpdateSchema) -> SessionOutSchema:
        """重命名会话。"""
        await self.crud.get_or_404(id=id, msg="修改失败，问答会话不存在")
        updated = await self.crud.update(id=id, data=data)
        return self._to_out(updated)

    async def delete(self, ids: list[int]) -> None:
        """删除会话：消息一并清理，避免孤儿数据继续参与统计。

        消息用物理删除（表里没有 deleted_id，CRUD 的软删判定不成立），
        会话仍走 CRUD 的默认口径（本项目缺 deleted_id → 物理删除），两者一致。
        """
        if not ids:
            raise CustomException(msg="删除失败，删除对象不能为空")
        await self.message_crud.delete_by_session(ids)
        await self.crud.delete(ids=ids)

    async def set_feedback(self, message_id: int, feedback: int) -> None:
        """记录答案反馈（1 有用 / -1 无用），仅允许对助手消息评价。"""
        message = await self.message_crud.get_or_404(id=message_id, msg="消息不存在")
        if message.role != 2:
            raise CustomException(msg="仅支持对助手回答进行反馈")
        await self.db.execute(
            update(IntegrationChatMessageModel)
            .where(IntegrationChatMessageModel.id == message_id)
            .values(feedback=feedback)
        )

    async def append_message(self, payload: MessageCreateSchema) -> int:
        """追加一条消息并同步会话计数（非流式场景用，例如人工补录）。"""
        message = await self.message_crud.create(data=payload)
        session = await self.crud.get(id=payload.session_id)
        if session is not None:
            await self.crud.update(
                id=payload.session_id,
                data={"message_count": (session.message_count or 0) + 1},
            )
        return int(message.id)

    @staticmethod
    def _to_out(obj: IntegrationChatSessionModel) -> SessionOutSchema:
        """会话模型 → 对外结构（session_id 统一转字符串，兼容前端既有封装）。"""
        data = SessionOutSchema.model_validate(obj)
        data.session_id = str(obj.id)
        return data

    @staticmethod
    def _to_message_out(obj: IntegrationChatMessageModel) -> MessageOutSchema:
        """消息模型 → 对外结构（role 整数转 user/assistant）。"""
        # role 库内为整数、对外契约为 str：必须先换好 role 再校验，
        # 否则 model_validate 会先卡在 string_type 上（Input should be a valid string）
        payload = {name: getattr(obj, name) for name in MessageOutSchema.model_fields if hasattr(obj, name)}
        payload["role"] = ROLE_LABEL.get(obj.role, "assistant")
        return MessageOutSchema.model_validate(payload)
