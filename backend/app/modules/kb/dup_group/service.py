"""重复分组治理服务：列表 / 详情 / 标记处理 / 检测结果物化（#16–#20）。"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from fastapi import status as http_status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_schema import PageResultSchema
from app.core.exceptions import CustomException
from app.utils.common_util import search_to_dict

from .crud import KbDupGroupCRUD
from .schema import KbDupGroupDetailOutSchema, KbDupGroupOutSchema, KbDupGroupQueryParam, KbDupGroupStatusSchema


@dataclass(frozen=True)
class KbDupGroupSyncData:
    """一次检测产出的分组快照（``dup_result`` 侧投影成这个结构再交给物化层）。

    刻意只带「可落库字段」：``group_key`` 已经蕴含成员身份，所以这里不需要判定
    「是不是同一个分组」，只需要刷新标题/相似度/依据这些会随内容漂移的展示字段。
    """

    tenant_id: str
    group_key: str
    kind: str
    title: str
    score_method: str
    reason: str
    suggested_keep_id: str | None
    result_id: str
    resource_type: str
    scope: dict[str, Any] | None
    members: list[dict[str, Any]]
    similarity: float


class KbDupGroupService:
    """重复分组服务。"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.crud = KbDupGroupCRUD(db)

    async def page(
        self,
        *,
        page_no: int,
        page_size: int,
        search: KbDupGroupQueryParam | None = None,
        order_by: list[dict[str, str]] | None = None,
    ) -> PageResultSchema[KbDupGroupOutSchema]:
        """分页查询：默认按创建时间倒序（最近检测结果在最前）。"""
        result = await self.crud.page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=order_by or [{"created_time": "desc"}],
            search=search_to_dict(search),
        )
        return PageResultSchema(
            page_no=result.page_no,
            page_size=result.page_size,
            total=result.total,
            has_next=result.has_next,
            items=[self._to_out(row) for row in result.items],
        )

    async def detail(self, group_id: int) -> KbDupGroupDetailOutSchema:
        """分组详情（含成员与合并预览）。"""
        row = await self.crud.get_or_404(id=group_id, msg="重复分组不存在")
        return KbDupGroupDetailOutSchema(
            **self._to_out(row).model_dump(),
            reason=row.reason or "",
            scope=row.scope,
            members=row.members or [],
            merged_preview=row.merged_preview,
        )

    async def update_status(self, group_id: int, data: KbDupGroupStatusSchema) -> KbDupGroupDetailOutSchema:
        """标记合并/忽略：只改治理状态，不动成员内容。"""
        row = await self.crud.get_or_404(id=group_id, msg="重复分组不存在", out_schema=None)
        if row.status != "active":
            raise CustomException(
                msg=f"分组已处于 {row.status} 状态，不能重复处理",
                status_code=http_status.HTTP_409_CONFLICT,
            )
        await self.crud.set(ids=[group_id], status=data.status)
        return await self.detail(group_id)

    async def sync_group(self, data: KbDupGroupSyncData) -> None:
        """把检测分组物化成治理行：新分组建行，老分组只刷新展示字段。

        **人工结论优先**：已 ``merged`` / ``dismissed`` 的分组不再回写成员与预览，
        仅记录「最近一次检测仍在报这条重复」，供管理端追溯重复是否复发。
        """
        values: dict[str, Any] = {
            "kind": data.kind,
            "title": data.title,
            "score_method": data.score_method,
            "reason": data.reason,
            "suggested_keep_id": data.suggested_keep_id,
            "result_id": data.result_id,
            "resource_type": data.resource_type,
            "scope": data.scope,
            "members": data.members,
            # 契约口径：分组相似度取「主项与各成员的最小值」，落库保持同一口径
            "similarity": Decimal(str(round(data.similarity, 4))),
        }
        existing = await self.crud.get_by_group_key(tenant_id=data.tenant_id, group_key=data.group_key)
        if existing is None:
            await self.crud.create(data={**values, "tenant_id": data.tenant_id, "group_key": data.group_key, "status": "active"})
            return
        if existing.status != "active":
            if existing.result_id != data.result_id:
                await self.crud.update(id=existing.id, data={"result_id": data.result_id})
            return
        # 成员展示信息变了，旧合并预览的文字依据就失效了：同一条 UPDATE 里一并作废
        await self.crud.refresh_active(
            tenant_id=data.tenant_id,
            group_key=data.group_key,
            values=values,
            drop_preview=(existing.members or []) != data.members,
        )

    @staticmethod
    def _to_out(row: Any) -> KbDupGroupOutSchema:
        """行 → 列表视图（列表不回 members 明细，只回条数）。"""
        return KbDupGroupOutSchema(
            id=row.id,
            uuid=row.uuid,
            created_time=row.created_time,
            updated_time=row.updated_time,
            tenant_id=row.tenant_id or "",
            group_key=row.group_key or "",
            kind=row.kind or "",
            title=row.title or "",
            score_method=row.score_method or "",
            suggested_keep_id=row.suggested_keep_id,
            result_id=row.result_id,
            resource_type=row.resource_type,
            status=row.status,
            similarity=float(row.similarity) if row.similarity is not None else None,
            member_count=len(row.members or []),
        )
