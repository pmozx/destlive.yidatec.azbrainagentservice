"""解析产物服务：产物与任务状态同事务写入（调用方给会话），查询自开短会话。"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_factory

from .crud import KbParseResultCRUD
from .model import KbParseResultModel
from .schema import KbParseResultCreateSchema


class KbParseResultService:
    """解析产物服务（不可变，只提供写入与按 job_id 查询）。"""

    async def save(self, data: KbParseResultCreateSchema, *, session: AsyncSession | None = None) -> None:
        """写入产物：``session`` 由任务服务传入时加入其事务（与状态更新一起提交）。"""
        if session is not None:
            await KbParseResultCRUD(session).create(data=data)
            return
        async with async_session_factory() as own_session, own_session.begin():
            await KbParseResultCRUD(own_session).create(data=data)

    async def get_by_job_id(self, job_id: str) -> KbParseResultModel | None:
        """按 job_id 读取产物；不存在返回 None。"""
        async with async_session_factory() as session:
            return await KbParseResultCRUD(session).get(job_id=job_id)
