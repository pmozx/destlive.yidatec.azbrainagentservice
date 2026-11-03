"""版本历史数据层：按快照归档（删除留墓碑）。"""

from datetime import UTC, datetime

from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_crud import CRUDBase

from .model import KbRevisionModel


class KbRevisionCRUD(CRUDBase[KbRevisionModel, BaseModel, BaseModel]):
    """版本历史数据层。"""

    def __init__(self, db: AsyncSession) -> None:
        super().__init__(model=KbRevisionModel, db=db)

    async def get_by_record(self, *, publication_id: str, record_id: str) -> KbRevisionModel | None:
        """按快照 ID 读取最新一条版本行（03.2 精读；删除墓碑同 record_id，取 version 最大者）。"""
        result = await self.db.execute(
            select(KbRevisionModel)
            .where(
                KbRevisionModel.publication_id == publication_id,
                KbRevisionModel.record_id == record_id,
            )
            .order_by(KbRevisionModel.revision.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def record_exists(self, *, publication_id: str, record_id: str) -> bool:
        """该快照 ID 是否已在该发布单元使用过（03.3 禁止复用历史 record_id）。"""
        result = await self.db.execute(
            select(KbRevisionModel.id)
            .where(
                KbRevisionModel.publication_id == publication_id,
                KbRevisionModel.record_id == record_id,
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def archive_by_record(self, *, publication_id: str, record_id: str) -> int:
        """把某快照的历史版本置为归档：行保留，作为防旧请求复活的墓碑。

        墓碑的状态只由 ``status='archived'`` + ``searchable=False`` 表达（软删字段已随基座移除）：
        ``status`` 本来就是版本状态机（online/archived），再加一个 ``is_deleted`` 会让
        "这个版本到底存不存在"有两个互相矛盾的答案。
        """
        now = datetime.now(UTC)
        result = await self.db.execute(
            update(KbRevisionModel)
            .where(
                KbRevisionModel.publication_id == publication_id,
                KbRevisionModel.record_id == record_id,
            )
            .values(status="archived", searchable=False, updated_time=now)
        )
        return int(getattr(result, "rowcount", 0) or 0)

    async def create_tombstone(
        self,
        *,
        publication_id: str,
        record_id: str,
        base_record_id: str | None,
        revision: int,
        version_id: str | None = None,
    ) -> None:
        """写入删除事件的版本墓碑（不可检索），保证 revision 单调不可复用。

        ``version_id`` 一并落库：#04 按版本精读时，删除事件也要能被查到并返回「已删除」，
        否则客户端拿着删除回执里的 version_id 回查会得到 404 而不是删除状态——
        这正是墓碑行需要 ``status='archived'`` 而不是被隐藏的原因。
        """
        await self.create(
            data={
                "publication_id": publication_id,
                "record_id": record_id,
                "version_id": version_id,
                "base_record_id": base_record_id,
                "revision": revision,
                "content_hash": None,
                "chunk_count": 0,
                "searchable": False,
                "status": "archived",
            }
        )
