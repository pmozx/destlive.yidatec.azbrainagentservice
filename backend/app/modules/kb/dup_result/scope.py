"""管理端治理范围生成（kb 基座帮助层）：从本地索引构造 :class:`ScopeData`。

契约 #16–#19 的 ``scope`` 由 GSI 随请求下发（授权资源 + 未发布草稿）。管理端没有 GSI 侧草稿，
只能以**已入库并生效**的内容为范围，所以这里按发布单元反查生成：

1. 只取 ``status=active``、``current_record_id`` 非空且当前快照 ``online`` 的发布单元——
   与检索同一口径，停用的发布单元不该再参与治理；
2. ``record_id`` / ``content_hash`` 一律取**当前生效快照**：``KbDupSource`` 会逐条比对，
   自己造 ID 只会换来 409；
3. 同一业务资源被多个团队发布时只保留一条（``identity = resource_type:resource_id``），
   ``publication_ids`` 合并——与 ``SpotResource.identity`` 的口径一致；
4. 范围里出现多个租户时要求调用方指定 ``tenant_id``：静默挑一个会让另一租户的资源被
   ``_assert_scope`` 判 403，错误信息离真正原因很远。
"""

from uuid import uuid4

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.setting import settings

from ..publication.model import KbPublicationModel
from ..publication.schema import PUBLICATION_ACTIVE
from ..revision.model import REVISION_ONLINE, KbRevisionModel
from .errors import DupInvalidRequestError, DupScopeTooLargeError
from .schema import ScopeData, SpotResource


async def build_scope_from_store(
    db: AsyncSession,
    *,
    tenant_id: str | None = None,
    publication_ids: list[str] | None = None,
) -> tuple[ScopeData, str]:
    """按发布单元生成治理范围，返回 ``(scope, 实际使用的 tenant_id)``。

    ``publication_ids`` 留空表示「全部生效发布单元」；给定时只治理这些发布单元，
    其中不存在的 ID 直接报错——静默忽略等于少比了内容还报成功。
    ``tenant_id`` 留空则按范围内发布单元的租户推导（混租户时要求显式指定）。
    """
    stmt = (
        select(KbPublicationModel, KbRevisionModel.content_hash)
        .join(
            KbRevisionModel,
            and_(
                KbRevisionModel.publication_id == KbPublicationModel.publication_id,
                KbRevisionModel.record_id == KbPublicationModel.current_record_id,
            ),
        )
        .where(
            KbPublicationModel.status == PUBLICATION_ACTIVE,
            KbPublicationModel.current_record_id.isnot(None),
            KbRevisionModel.status == REVISION_ONLINE,
            KbRevisionModel.content_hash.isnot(None),
        )
    )
    if tenant_id:
        stmt = stmt.where(KbPublicationModel.tenant_id == tenant_id)
    if publication_ids:
        stmt = stmt.where(KbPublicationModel.publication_id.in_(publication_ids))
    rows = (await db.execute(stmt)).all()

    if publication_ids:
        missing = {
            publication_id
            for publication_id in publication_ids
            if publication_id not in {row[0].publication_id for row in rows}
        }
        if missing:
            raise DupInvalidRequestError(
                f"发布单元 {sorted(missing)} 不存在、未生效或没有可检索快照，不能作为治理范围。"
            )
    if not rows:
        raise DupInvalidRequestError("治理范围内没有生效的知识内容，请先完成知识写入。")

    merged: dict[str, dict[str, object]] = {}
    tenants: set[str] = set()
    for publication, content_hash in rows:
        if publication.tenant_id:
            tenants.add(publication.tenant_id)
        resource_id = publication.resource_id or publication.publication_id
        identity = f"{publication.resource_type}:{resource_id}"
        item = merged.get(identity)
        if item is None:
            merged[identity] = {
                "resource_type": publication.resource_type,
                "resource_id": resource_id,
                "record_id": publication.current_record_id,
                "content_hash": f"sha256:{content_hash}",
                "publication_ids": [publication.publication_id],
                "entry_mode": "manual",
            }
        else:
            item["publication_ids"].append(publication.publication_id)  # type: ignore[union-attr]

    limit = settings.DUP_MAX_SCOPE_ITEMS
    if len(merged) > limit:
        raise DupScopeTooLargeError(
            f"治理范围最多 {limit} 条内容，本次命中 {len(merged)} 条，请指定 publication_ids 缩小范围。"
        )
    if tenant_id is None and len(tenants) > 1:
        raise DupInvalidRequestError(f"治理范围包含多个租户 {sorted(tenants)}，请指定 tenant_id。")

    resources = tuple(
        SpotResource(
            resource_type=str(item["resource_type"]),
            resource_id=str(item["resource_id"]),
            record_id=str(item["record_id"]),
            content_hash=str(item["content_hash"]),
            publication_ids=tuple(item["publication_ids"]),  # type: ignore[arg-type]
            entry_mode="manual",
        )
        for item in merged.values()
    )
    scope = ScopeData(scope_id=f"admin-scope-{uuid4().hex[:12]}", resources=resources)
    return scope, tenant_id or (next(iter(tenants)) if tenants else "")


__all__ = ["build_scope_from_store"]
