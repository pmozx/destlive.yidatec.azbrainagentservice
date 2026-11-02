"""发布单元数据层。"""

from typing import Any

from pydantic import BaseModel
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_crud import CRUDBase

from .model import KbPublicationModel


class KbPublicationCRUD(CRUDBase[KbPublicationModel, BaseModel, BaseModel]):
    """发布单元数据层。"""

    def __init__(self, db: AsyncSession) -> None:
        super().__init__(model=KbPublicationModel, db=db)

    async def advance(
        self,
        *,
        publication_id: str,
        expected_revision: int,
        values: dict[str, Any],
    ) -> bool:
        """按 ``current_revision`` 做条件更新（CAS），返回是否命中该行。

        修订与删除都是「读当前指针 → 校验 → 写新指针」，两步之间没有锁：
        两个并发请求都能通过校验，后写的那个会把 revision 覆盖回去，破坏单调性
        （进而让墓碑防复活失效）。把期望版本写进 WHERE 后，落败的一方 rowcount=0，
        由 Service 转成 409 ``STALE_REVISION``，语义与「基线已被他人修改」一致。

        注意：``expected_revision`` 是**校验通过时读到的版本**，不是请求里的新版本。
        """
        result = await self.db.execute(
            update(KbPublicationModel)
            .where(
                KbPublicationModel.publication_id == publication_id,
                KbPublicationModel.current_revision == expected_revision,
            )
            .values(**values)
        )
        return result.rowcount == 1
