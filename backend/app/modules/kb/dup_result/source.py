"""治理范围取内容（kb 基座帮助层）：``scope`` 清单 → 可比对的 :class:`GovernanceItem`。

只读，不写任何表。三条硬规则（§九）：

1. **只比较清单里列出的内容**：没列出的发布单元一律不查，避免「顺手多比」变成越权；
2. **正式内容必须与 scope 声明一致**：``record_id`` 仍是当前生效快照、``content_hash`` 与本地
   版本行一致；任一不符即来源变化（409），绝不拿旧内容继续比较；
3. **归属不一致直接拒绝**（租户 / 资源类型 / 资源 ID），不降级放行。

为什么不是「读不到就跳过」：跳过会让 ``coverage.evaluated_resources < requested_resources``，
而契约要求 200 时两者必须相等；用不完整检测冒充成功，正是「失败显示未发现重复」的另一种写法。
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.kb.chunk.crud import KbChunkCRUD
from app.modules.kb.publication.crud import KbPublicationCRUD
from app.modules.kb.publication.schema import PUBLICATION_ACTIVE
from app.modules.kb.revision.crud import KbRevisionCRUD

from .errors import DupScopeDeniedError, DupSourceChangedError
from .schema import GovernanceItem, ScopeData, SpotChunk, SpotDraft, SpotResource


def hash_matches(declared: str, stored: str | None) -> bool:
    """摘要比对：契约值是 ``sha256:<hex>``，落库按 varchar(64) 只存 hex，两边都取最后一段比。"""
    if not stored:
        return False
    return declared.rsplit(":", 1)[-1].strip().lower() == stored.strip().lower()


class KbDupSource:
    """按授权范围还原参与比较的内容（正式内容取自本地索引，草稿取自请求正文）。"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.publication_crud = KbPublicationCRUD(db)
        self.revision_crud = KbRevisionCRUD(db)
        self.chunk_crud = KbChunkCRUD(db)

    async def load(self, *, scope: ScopeData, tenant_id: str) -> dict[str, GovernanceItem]:
        """返回 ``identity → GovernanceItem``；清单外的草稿不参与比较（AI 只能比较清单内内容）。"""
        drafts = {draft.resource.identity: draft for draft in scope.drafts}
        items: dict[str, GovernanceItem] = {}
        for resource in scope.resources:
            draft = drafts.get(resource.identity)
            if draft is not None:
                items[resource.identity] = self._from_draft(draft)
            else:
                items[resource.identity] = await self._from_store(resource, tenant_id=tenant_id)
        return items

    @staticmethod
    def _from_draft(draft: SpotDraft) -> GovernanceItem:
        """草稿内容由调用方随请求提供，直接参与比较，不落任何正式表。"""
        return GovernanceItem(
            resource=draft.resource,
            title=draft.title,
            content=draft.content,
            summary=draft.summary,
            chunks=draft.chunks,
            is_draft=True,
            created_time=None,
        )

    async def _from_store(self, resource: SpotResource, *, tenant_id: str) -> GovernanceItem:
        publication = await self._resolve_publication(resource)
        self._assert_scope(publication, resource, tenant_id=tenant_id)
        self._assert_effective(publication, resource)

        revision = await self.revision_crud.get_by_record(
            publication_id=publication.publication_id, record_id=resource.record_id
        )
        if revision is None or revision.content_hash is None or revision.status != "online":
            raise DupSourceChangedError(
                f"资源 {resource.identity} 的快照 {resource.record_id} 不存在或已归档，请重新发起检测。"
            )
        if not hash_matches(resource.content_hash, revision.content_hash):
            raise DupSourceChangedError(
                f"资源 {resource.identity} 的内容摘要已变化（scope 声明与本地版本不一致），请重新发起检测。"
            )

        rows = await self.chunk_crud.list_by_record(
            publication_id=publication.publication_id, record_id=resource.record_id
        )
        chunks = tuple(
            SpotChunk(seq=row.seq, content=row.content, chunk_id=row.chunk_id, title=row.heading) for row in rows
        )
        return GovernanceItem(
            resource=resource,
            title=publication.title,
            content="\n".join(chunk.content for chunk in chunks),
            summary=publication.summary,
            chunks=chunks,
            is_draft=False,
            created_time=revision.created_time,
        )

    async def _resolve_publication(self, resource: SpotResource):
        """按 ``publication_ids`` 顺序找第一条存在的发布单元（同一内容多团队发布只比一次）。"""
        for publication_id in resource.publication_ids:
            publication = await self.publication_crud.get(publication_id=publication_id)
            if publication is not None:
                return publication
        raise DupSourceChangedError(
            f"资源 {resource.identity} 在授权范围内没有已同步的发布单元，请重新发起检测。"
        )

    @staticmethod
    def _assert_scope(publication: Any, resource: SpotResource, *, tenant_id: str) -> None:
        if publication.resource_type != resource.resource_type:
            raise DupScopeDeniedError(
                f"资源 {resource.identity} 的类型为 {resource.resource_type}，本地发布单元为 "
                f"{publication.resource_type}，不能参与本次治理。"
            )
        if publication.resource_id and publication.resource_id != resource.resource_id:
            raise DupScopeDeniedError(f"资源 {resource.identity} 与本地发布单元归属的资源不一致。")
        if publication.tenant_id and publication.tenant_id != tenant_id:
            raise DupScopeDeniedError(f"资源 {resource.identity} 不属于租户 {tenant_id}，无权治理。")

    @staticmethod
    def _assert_effective(publication: Any, resource: SpotResource) -> None:
        """必须仍指向 scope 声明的那个快照：指针推进或停用都算来源变化。"""
        if publication.status != PUBLICATION_ACTIVE or publication.current_record_id != resource.record_id:
            raise DupSourceChangedError(
                f"资源 {resource.identity} 的生效快照已变化（当前为 {publication.current_record_id}），请重新发起检测。"
            )
