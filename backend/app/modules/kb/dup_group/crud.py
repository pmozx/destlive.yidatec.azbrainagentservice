"""重复分组数据层：只做数据访问，「新建还是复用」的判定留在 service 层。"""

from typing import Any

from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_crud import CRUDBase

from .model import KbDupGroupModel


class KbDupGroupCRUD(CRUDBase[KbDupGroupModel, BaseModel, BaseModel]):
    """``sbp_ai_kb_dup_group`` 数据层。"""

    def __init__(self, db: AsyncSession) -> None:
        super().__init__(model=KbDupGroupModel, db=db)

    async def get_by_group_key(self, *, tenant_id: str, group_key: str) -> KbDupGroupModel | None:
        """按「租户 + 契约 group_id」读取治理行（唯一索引 ``uq_sbp_ai_kb_dup_group_tenant_key``）。"""
        result = await self.db.execute(
            select(KbDupGroupModel)
            .where(
                KbDupGroupModel.tenant_id == tenant_id,
                KbDupGroupModel.group_key == group_key,
            )
            .limit(1)
        )
        return result.scalars().first()

    async def refresh_active(
        self,
        *,
        tenant_id: str,
        group_key: str,
        values: dict[str, Any],
        drop_preview: bool,
    ) -> bool:
        """回写仍处于 ``active`` 的治理行（CAS：人工已 merged/dismissed 时返回 False）。

        ``drop_preview`` 为真说明成员集合变了，旧的合并预览不能再展示，一并清空——
        一次 UPDATE 同时表达「改成员」和「预览作废」，避免两次写之间出现新旧混排。
        """
        stmt = (
            update(KbDupGroupModel)
            .where(
                KbDupGroupModel.tenant_id == tenant_id,
                KbDupGroupModel.group_key == group_key,
                KbDupGroupModel.status == "active",
            )
            .values(**values)
        )
        if drop_preview:
            stmt = stmt.values(merged_preview=None)
        result = await self.db.execute(stmt)
        await self.db.flush()
        return int(getattr(result, "rowcount", 0) or 0) == 1
