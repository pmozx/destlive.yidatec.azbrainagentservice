"""重复分组治理服务：列表 / 详情 / 标记处理 / 检测结果物化（#16–#20）。

管理端主动治理（检测 / 查重 / 合并预览）复用 ``dup_result`` 的领域方法，本文件只负责
「范围从哪来 + 错误码翻成管理端口径 + 投影成页面结构」：

- 范围由 ``dup_result.scope.build_scope_from_store`` 按发布单元反查（管理端没有 GSI 的 scope 签发方）；
- ``ADMIN_USER_ID`` 为固定操作者：检测时写入快照，预览时按同一值读回——不同值会命中 404
 （快照按 ``user_id`` 归属），而管理端本来就是无登录上下文的运维台。
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import uuid4

from fastapi import status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_schema import PageResultSchema
from app.core.exceptions import CustomException
from app.core.logger import logger
from app.utils.common_util import search_to_dict

from ..dup_result.errors import (
    DupInvalidRequestError,
    DupResultExpiredError,
    DupResultNotFoundError,
    DupScopeDeniedError,
    DupScopeTooLargeError,
    DupSourceChangedError,
)
from ..dup_result.schema import (
    DuplicateCheckData,
    DuplicateGroupsQuery,
    GovernanceItem,
    MergeActionData,
    MergePreviewData,
    ScopeData,
)
from ..dup_result.scope import build_scope_from_store
from .crud import KbDupGroupCRUD
from .schema import (
    KbDupCheckOutSchema,
    KbDupCheckSchema,
    KbDupDetectOutSchema,
    KbDupDetectSchema,
    KbDupGroupDetailOutSchema,
    KbDupGroupOutSchema,
    KbDupGroupQueryParam,
    KbDupGroupStatusSchema,
    KbDupPreviewOutSchema,
    KbDupPreviewSchema,
)

#: 治理快照的归属用户：固定值，保证「检测 → 预览」读到同一份快照
ADMIN_USER_ID = "admin"


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
        # 延迟导入：``dup_result.service`` 反向依赖本模块的 ``sync_group``（物化），
        # 模块级导入会成环（与 ``dup_result/__init__`` 的注释同一个原因）
        from ..dup_result.service import KbDupResultService

        self.db = db
        self.crud = KbDupGroupCRUD(db)
        # 检测/预览自开会话（快照与物化同事务），与本服务读会话互不干扰
        self.results = KbDupResultService()

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
                status_code=status.HTTP_409_CONFLICT,
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

    async def detect(self, data: KbDupDetectSchema) -> KbDupDetectOutSchema:
        """触发一次检测：范围内全量比对，产物落 30 分钟快照并物化治理分组。

        与 ``groups/query`` 的差别只在范围来源：契约由 GSI 签发 scope，管理端由发布单元反查。
        结果会写入 ``dup_group``（见 ``dup_result.service._materialize``），所以本接口返回后
        列表页能立刻看到新分组。
        """
        scope, tenant_id = await self._scope(data)
        query = DuplicateGroupsQuery(
            request_id=f"admin-detect-{uuid4().hex[:12]}",
            tenant_id=tenant_id,
            user_id=ADMIN_USER_ID,
            scope=scope,
            kinds=tuple(data.kinds),
            score_profile=data.score_profile,
            cursor=0,
            limit=data.limit,
        )
        try:
            page = await self.results.groups(query)
        except Exception as exc:
            raise self._map_error(exc) from exc
        return KbDupDetectOutSchema(
            result_id=page.result_id,
            expires_at=page.expires_at,
            score_profile=page.score_profile,
            scope_size=page.coverage.requested_resources,
            total_groups=page.total_groups,
            returned_groups=len(page.groups),
            coverage=page.coverage.to_payload(),
            items=[group.to_payload() for group in page.groups],
        )

    async def check(self, data: KbDupCheckSchema) -> KbDupCheckOutSchema:
        """保存前查重：候选内容 vs 治理范围，只报命中、不阻止保存。"""
        scope, tenant_id = await self._scope(data)
        candidate = GovernanceItem.from_payload(
            {
                "resource": {
                    "resource_type": data.candidate.resource_type,
                    "resource_id": data.candidate.resource_id,
                    "record_id": data.candidate.record_id,
                    "content_hash": data.candidate.content_hash,
                    "publication_ids": [],
                    "entry_mode": "manual",
                },
                "title": data.candidate.title,
                "summary": data.candidate.summary,
                "content": data.candidate.content,
                "chunks": [chunk.model_dump() for chunk in data.candidate.chunks],
            }
        )
        query = DuplicateCheckData(
            request_id=f"admin-check-{uuid4().hex[:12]}",
            tenant_id=tenant_id,
            user_id=ADMIN_USER_ID,
            candidate=candidate,
            scope=scope,
            score_profile=data.score_profile,
            kinds=tuple(data.kinds),
        )
        try:
            outcome = await self.results.check(query)
        except Exception as exc:
            raise self._map_error(exc) from exc
        return KbDupCheckOutSchema(
            request_id=outcome.request_id,
            is_duplicate=outcome.is_duplicate,
            similarity=outcome.similarity,
            score_profile=outcome.score_profile,
            scope_size=scope.requested_resources,
            matches=[match.to_payload() for match in outcome.matches],
        )

    async def preview(self, group_id: int, data: KbDupPreviewSchema) -> KbDupPreviewOutSchema:
        """生成合并预览：范围与 result_id 取自分组行，结果由领域层写回 ``merged_preview``。

        ``result_id`` 指向 30 分钟快照，过期会得到 409（重新发起检测即可）——不回旧快照，
        否则预览的依据与当前内容可能已经不一致。
        """
        row = await self.crud.get_or_404(id=group_id, msg="重复分组不存在", out_schema=None)
        if not row.scope:
            raise CustomException(
                msg="该分组没有检测范围快照，请重新发起检测后再生成合并预览",
                status_code=status.HTTP_409_CONFLICT,
            )
        if not row.result_id:
            raise CustomException(
                msg="该分组没有关联的检测结果，请重新发起检测后再生成合并预览",
                status_code=status.HTTP_409_CONFLICT,
            )
        keep_hit_id = data.keep_hit_id or row.suggested_keep_id or ""
        if not keep_hit_id:
            raise CustomException(msg="请指定保留项 keep_hit_id（该分组没有建议主项）")

        query = MergePreviewData(
            request_id=f"admin-preview-{uuid4().hex[:12]}",
            tenant_id=row.tenant_id or "",
            user_id=ADMIN_USER_ID,
            result_id=row.result_id,
            group_id=row.group_key,
            scope=ScopeData.from_payload(row.scope),
            keep_hit_id=keep_hit_id,
            actions=tuple(
                MergeActionData(
                    target_hit_id=action.target_hit_id,
                    action=action.action,
                    merged_answer_html=action.merged_answer_html,
                )
                for action in data.actions
            ),
            prompt_text=data.prompt_text,
            model_key=data.model_key,
        )
        try:
            outcome = await self.results.preview(query)
        except Exception as exc:
            raise self._map_error(exc) from exc
        return KbDupPreviewOutSchema(
            preview_id=outcome.preview_id,
            preview_hash=outcome.preview_hash,
            title=outcome.title,
            answer_html=outcome.answer_html,
            summary=outcome.summary,
            source_hit_ids=list(outcome.source_hit_ids),
            conflicts=[conflict.to_payload() for conflict in outcome.conflicts],
            warnings=list(outcome.warnings),
        )

    async def _scope(self, data: KbDupDetectSchema | KbDupCheckSchema) -> tuple[ScopeData, str]:
        """按发布单元生成治理范围（领域异常翻成管理端口径）。"""
        try:
            return await build_scope_from_store(
                self.db,
                tenant_id=data.tenant_id,
                publication_ids=data.publication_ids or None,
            )
        except Exception as exc:
            raise self._map_error(exc) from exc

    @staticmethod
    def _map_error(exc: Exception) -> CustomException:
        """领域异常 → 管理端状态码（文档 §1.7 口径）；领域层不感知 HTTP。"""
        if isinstance(exc, DupScopeTooLargeError):
            return CustomException(msg=str(exc), status_code=status.HTTP_413_CONTENT_TOO_LARGE)
        if isinstance(exc, DupScopeDeniedError):
            return CustomException(msg=str(exc), status_code=status.HTTP_403_FORBIDDEN)
        if isinstance(exc, (DupSourceChangedError, DupResultExpiredError)):
            return CustomException(msg=str(exc), status_code=status.HTTP_409_CONFLICT)
        if isinstance(exc, DupResultNotFoundError):
            return CustomException(msg=str(exc), status_code=status.HTTP_404_NOT_FOUND)
        if isinstance(exc, DupInvalidRequestError):
            return CustomException(msg=str(exc))
        logger.exception("管理端重复治理执行失败：{}", exc)
        return CustomException(
            msg="重复治理执行失败，请查看服务端日志",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
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
