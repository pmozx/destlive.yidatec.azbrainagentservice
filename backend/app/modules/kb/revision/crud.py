"""版本历史数据层：按快照归档（删除留墓碑）。"""

from datetime import UTC, datetime

from pydantic import BaseModel
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_crud import CRUDBase

from .model import KbRevisionModel


class KbRevisionCRUD(CRUDBase[KbRevisionModel, BaseModel, BaseModel]):
    """版本历史数据层。"""

    def __init__(self, db: AsyncSession) -> None:
        super().__init__(model=KbRevisionModel, db=db)

    async def archive_by_record(self, *, publication_id: str, record_id: str) -> int:
        """把某快照的历史版本置为归档并软删：行仍保留，作为防旧请求复活的墓碑。"""
        now = datetime.now(UTC)
        result = await self.db.execute(
            update(KbRevisionModel)
            .where(
                KbRevisionModel.publication_id == publication_id,
                KbRevisionModel.record_id == record_id,
            )
            .values(status="archived", searchable=False, is_deleted=True, deleted_time=now, updated_time=now)
        )
        return int(getattr(result, "rowcount", 0) or 0)

    async def create_tombstone(self, *, publication_id: str, record_id: str, base_record_id: str | None, revision: int) -> None:
        """写入删除事件的版本墓碑（不可检索、已软删），保证 revision 单调不可复用。"""
        now = datetime.now(UTC)
        await self.create(
            data={
                "publication_id": publication_id,
                "record_id": record_id,
                "base_record_id": base_record_id,
                "revision": revision,
                "content_hash": None,
                "chunk_count": 0,
                "searchable": False,
                "status": "archived",
                "is_deleted": True,
                "deleted_time": now,
            }
        )
