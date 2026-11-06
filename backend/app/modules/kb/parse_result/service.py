"""解析产物服务：产物与任务状态同事务写入（调用方给会话），查询自开短会话。"""
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_factory

from .crud import KbParseResultCRUD
from .model import KbParseResultModel
from .schema import KbParseResultCreateSchema


class KbParseResultService:
    """解析产物服务（按 job_id 覆盖写入 + 按 job_id 查询）。"""

    async def save(self, data: KbParseResultCreateSchema, *, session: AsyncSession | None = None) -> None:
        """写入产物：``session`` 由任务服务传入时加入其事务（与状态更新一起提交）。

        同一 job_id 已有产物时覆盖内容列——重跑语义（断点续跑/重新解析）必须能覆盖
        上一轮的快照，而不是撞 job_id 唯一约束；``publication_id`` / ``published_time``
        是「已写入知识平台」的溯源，重跑不清（否则页面会以为从未发布过）。
        """
        if session is not None:
            await self._save(KbParseResultCRUD(session), data)
            return
        async with async_session_factory() as own_session, own_session.begin():
            await self._save(KbParseResultCRUD(own_session), data)

    @staticmethod
    async def _save(crud: KbParseResultCRUD, data: KbParseResultCreateSchema) -> None:
        existing = await crud.get(job_id=data.job_id)
        if existing is None:
            await crud.create(data=data)
            return
        await crud.update(
            id=existing.id,
            data=data.model_dump(exclude={"job_id"}),
        )

    async def get_by_job_id(self, job_id: str) -> KbParseResultModel | None:
        """按 job_id 读取产物；不存在返回 None。"""
        async with async_session_factory() as session:
            return await KbParseResultCRUD(session).get(job_id=job_id)
