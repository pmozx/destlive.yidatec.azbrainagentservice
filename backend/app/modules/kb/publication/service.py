"""发布单元管理服务：列表 / 详情 / 新增 / 编辑 / 状态 / 删除 / 版本 / 切片。

契约写入链路（03.1 / 03.2 / 03.3 / 03.4）在 :mod:`.entry_service`，两个类的调用方与
事务边界都不同：

- **KbEntryService**：只被 ``modules_api/kb/knowledge/service.py`` 调用，自持
  ``async_session_factory`` 事务；
- **KbPublicationService**（本文件）：被 ``modules/kb/publication/controller.py`` 调用，
  注入 ``self.db``，写正文时用 ``EntryWriteData`` 把落库交给 ``KbEntryService``。

管理端正文写入与管理端其它写操作同引擎：``write_entry`` / ``write_entry_from_chunks`` /
``revise_entry`` / ``deactivate`` 只做「切片准备 + 摘要 + 展示字段同步 + 异常映射」，不另写一套落库。
写入口径（向量化先于事务、全量替换 + 指针切换、CAS 并发控制）见 :mod:`.entry_service`。

两个写入入口只差切片的来源：``write_entry`` 把整篇正文交给 ``split_text``；
``write_entry_from_chunks`` 直接用调用方给定的已批准切片（如解析产物，已含人工修订）。
"""

from dataclasses import asdict
from typing import Any
from uuid import uuid4

from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_schema import PageResultSchema
from app.core.exceptions import CustomException
from app.utils.common_util import search_to_dict
from app.utils.text_splitter import split_text

from ..chunk.crud import KbChunkCRUD
from ..parse_job.model import KbParseJobModel
from ..revision.crud import KbRevisionCRUD
from .crud import KbPublicationCRUD
from .entry_service import (
    EntryConflictError,
    EntryEmbeddingError,
    EntryNotFoundError,
    EntryScopeError,
    KbEntryService,
)
from .model import KbPublicationModel
from .schema import (
    BaselineQuery,
    EntryChunkData,
    EntryDeactivateData,
    EntryDeleteData,
    EntryReceipt,
    EntryWriteData,
    KbPublicationBaselineOutSchema,
    KbPublicationChunkDetailOutSchema,
    KbPublicationChunkOutSchema,
    KbPublicationCreateSchema,
    KbPublicationDeactivateOutSchema,
    KbPublicationDeactivateSchema,
    KbPublicationDetailOutSchema,
    KbPublicationEntryChunkSchema,
    KbPublicationEntryChunksWriteSchema,
    KbPublicationEntryReviseSchema,
    KbPublicationEntryWriteOutSchema,
    KbPublicationEntryWriteSchema,
    KbPublicationOutSchema,
    KbPublicationParseJobBriefOutSchema,
    KbPublicationQueryParam,
    KbPublicationRevisionDetailOutSchema,
    KbPublicationRevisionOutSchema,
    KbPublicationSnapshotDeleteOutSchema,
    KbPublicationSnapshotDeleteSchema,
    KbPublicationSourceOutSchema,
    KbPublicationStatusSchema,
    KbPublicationUpdateSchema,
    PUBLICATION_ACTIVE,
    PUBLICATION_DELETED,
    PUBLICATION_INACTIVE,
    canonical_content_hash,
)

# ── 状态常量（发布单元取自 schema，版本状态取自 revision 模型，保持单一取值来源）──

# 管理端写入、停用、删除快照共用同一个操作人：快照的 user_id 仅用于留痕，不参与授权
ADMIN_USER_ID: str = "admin"

# 管理端生成的快照 ID 前缀：契约由 GSI 生成 record_id，靠这个前缀反向判定「谁写的」（来源溯源用）
ADMIN_RECORD_PREFIX: str = "admin-"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 管理端维护服务
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class KbPublicationService:
    """发布单元管理服务（列表 / 详情 / 新增 / 编辑 / 状态 / 删除 / 版本 / 切片）。"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.crud = KbPublicationCRUD(db)

    # ── 查询 ──────────────────────────────────────────────────────────

    async def page(
        self,
        *,
        page_no: int,
        page_size: int,
        search: KbPublicationQueryParam | None = None,
        order_by: list[dict[str, str]] | None = None,
    ) -> PageResultSchema[KbPublicationOutSchema]:
        """分页查询：单表操作，支持标题/分类/状态/类型过滤。"""
        return await self.crud.page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=order_by or [{"created_time": "desc"}],
            search=search_to_dict(search),
            out_schema=KbPublicationOutSchema,
        )

    async def detail(self, publication_id: str) -> KbPublicationDetailOutSchema:
        """详情：发布单元全字段 + 版本数/切片数。"""
        return await self._detail_out(await self._ensure_publication(publication_id))

    async def _detail_out(self, publication: KbPublicationModel) -> KbPublicationDetailOutSchema:
        """详情出参：发布单元全字段 + 版本数/切片数（详情 / 编辑 / 上下线共用）。"""
        out = KbPublicationDetailOutSchema.model_validate(publication)
        out.version_count = await self.crud.count_versions(publication.publication_id)
        out.chunk_count = await self.crud.count_chunks(publication.publication_id)
        return out

    async def source(self, publication_id: str) -> KbPublicationSourceOutSchema:
        """来源与关联（只读）：判定写入渠道，并按 ``file_meta.doc_id`` 反查解析任务。

        发布单元与解析任务之间没有库级关联（见 ``02-数据模型与表关系.md`` §1.2「内容与作业分离」）：
        解析产物是「AI 看到什么」，知识切片是「审批后确认什么」。因此这里只回线索——
        契约 #01 把 ``doc_id`` 落在 ``parse_job.file_meta`` 里，用它比对 ``publication.resource_id``；
        管理台上传的任务没有 ``doc_id``，永远匹配不上，页面需按「仅供参考」展示。
        """
        publication = await self._ensure_publication(publication_id)
        jobs: list[KbPublicationParseJobBriefOutSchema] = []
        if publication.resource_id:
            rows = await self.db.scalars(
                select(KbParseJobModel)
                .where(KbParseJobModel.file_meta["doc_id"].astext == publication.resource_id)
                .order_by(KbParseJobModel.created_time.desc())
                .limit(20)
            )
            jobs = [
                KbPublicationParseJobBriefOutSchema(
                    job_id=row.uuid,
                    status=row.status,
                    progress=row.progress,
                    resource_type=row.resource_type,
                    created_time=row.created_time,
                )
                for row in rows.all()
            ]
        return KbPublicationSourceOutSchema(
            publication_id=publication.publication_id,
            resource_type=publication.resource_type,
            resource_id=publication.resource_id,
            tenant_id=publication.tenant_id,
            status=publication.status,
            current_record_id=publication.current_record_id,
            current_revision=publication.current_revision,
            write_channel=self._write_channel(publication),
            parse_jobs=jobs,
            hint=(
                "线索来源：解析任务的 file_meta.doc_id 与发布单元 resource_id 比对，不构成强关联；"
                "管理台上传的解析任务没有 doc_id，不会出现在这里。"
            ),
        )

    @staticmethod
    def _write_channel(publication: KbPublicationModel) -> str:
        """写入渠道：无快照 = pending，``admin-`` 前缀 = 管理台补录/修订，其余 = GSI 契约写入。"""
        if not publication.current_record_id:
            return "pending"
        return "admin" if publication.current_record_id.startswith(ADMIN_RECORD_PREFIX) else "contract"

    async def list_revisions(
        self,
        *,
        publication_id: str,
        page_no: int,
        page_size: int,
        order_by: list[dict[str, str]] | None = None,
    ) -> PageResultSchema[KbPublicationRevisionOutSchema]:
        """版本历史分页：校验发布单元存在后按版本号倒序。"""
        await self._ensure_publication(publication_id)
        result = await KbRevisionCRUD(self.db).page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=order_by or [{"revision": "desc"}],
            search={"publication_id": publication_id},
            out_schema=KbPublicationRevisionOutSchema,
        )
        return result

    async def detail_revision(self, revision_id: int) -> KbPublicationRevisionDetailOutSchema:
        """版本详情（含契约透传元数据）。"""
        return await KbRevisionCRUD(self.db).get_or_404(
            id=revision_id,
            msg="版本快照不存在",
            out_schema=KbPublicationRevisionDetailOutSchema,
        )

    async def list_chunks(
        self,
        *,
        publication_id: str,
        record_id: str | None = None,
        page_no: int,
        page_size: int,
        order_by: list[dict[str, str]] | None = None,
    ) -> PageResultSchema[KbPublicationChunkOutSchema]:
        """切片分页：可传 record_id 只看某版本快照。"""
        await self._ensure_publication(publication_id)
        search: dict[str, Any] = {"publication_id": publication_id}
        if record_id:
            search["record_id"] = record_id
        return await KbChunkCRUD(self.db).page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=order_by or [{"seq": "asc"}],
            search=search,
            out_schema=KbPublicationChunkOutSchema,
        )

    async def detail_chunk(self, chunk_id: int) -> KbPublicationChunkDetailOutSchema:
        """切片详情（管理端只读查看正文）。"""
        return await KbChunkCRUD(self.db).get_or_404(
            id=chunk_id,
            msg="知识切片不存在",
            out_schema=KbPublicationChunkDetailOutSchema,
        )

    async def read_baseline(
        self,
        *,
        publication_id: str,
        record_id: str | None = None,
        cursor: str | None = None,
        limit: int = 100,
    ) -> KbPublicationBaselineOutSchema:
        """03.2 读取知识修订基线：未指定 ``record_id`` 时读当前生效快照。

        与 ``/v1`` 共用 ``KbEntryService.read_baseline``，保证维护页看到的
        切片、摘要与 GSI 联调一致；游标沿用契约口径（上一页最后一个切片的 ``seq``）。
        """
        publication = await self._ensure_publication(publication_id)
        target_record = record_id or publication.current_record_id
        if not target_record:
            raise CustomException(msg="该发布单元暂无已批准快照", status_code=status.HTTP_404_NOT_FOUND)

        try:
            baseline = await KbEntryService().read_baseline(
                BaselineQuery(
                    publication_id=publication_id,
                    tenant_id=publication.tenant_id or "",
                    user_id="admin",
                    record_id=target_record,
                    after_seq=self._parse_cursor(cursor),
                    limit=limit,
                )
            )
        except EntryNotFoundError as exc:
            raise CustomException(msg=str(exc), status_code=status.HTTP_404_NOT_FOUND) from exc
        except EntryScopeError as exc:
            raise CustomException(msg=str(exc), status_code=status.HTTP_403_FORBIDDEN) from exc

        return KbPublicationBaselineOutSchema.model_validate(baseline)

    # ── 写入 ──────────────────────────────────────────────────────────

    async def create(self, data: KbPublicationCreateSchema) -> KbPublicationDetailOutSchema:
        """新增发布单元：单表创建，同时承载契约指针与管理端展示字段。

        业务校验：
        - ``publication_id`` 全局唯一，重复创建直接 409（先查后写只是提前报错，真正的互斥由唯一键兜底）；
        - ``resource_type`` 由 Schema 的 Literal 白名单兜住，这里不再重复校验。
        """
        pub_crud = KbPublicationCRUD(self.db)
        existing = await pub_crud.get(publication_id=data.publication_id)
        if existing is not None:
            raise CustomException(
                msg=f"发布单元 {data.publication_id} 已存在，不能重复创建",
                status_code=status.HTTP_409_CONFLICT,
            )

        await pub_crud.create(
            data={
                "publication_id": data.publication_id,
                "tenant_id": data.tenant_id,
                "resource_id": data.resource_id,
                "resource_type": data.resource_type,
                "current_record_id": None,
                "current_revision": 0,
                "last_synced_at": None,
                "status": "active",
                # 管理端展示字段
                "title": data.title,
                "category": data.category,
                "summary": data.summary,
                "tags": data.tags,
                "owner": data.owner,
            }
        )
        return await self.detail(data.publication_id)

    async def update(
        self, publication_id: str, data: KbPublicationUpdateSchema
    ) -> KbPublicationDetailOutSchema:
        """编辑发布单元：单表更新所有字段（显式传 null 表示清空可空字段）。"""
        publication = await self._ensure_publication(publication_id)

        # exclude_unset 保留调用方显式传入的 null（清空语义），未传的字段不参与更新
        for key, value in data.model_dump(exclude_unset=True).items():
            if hasattr(publication, key):
                setattr(publication, key, value)
        await self.db.flush()
        await self.db.refresh(publication)

        return await self._detail_out(publication)

    async def update_status(self, publication_id: str, data: KbPublicationStatusSchema) -> KbPublicationDetailOutSchema:
        """上下线：只改发布单元状态（active/inactive/deleted），不动内容与版本。"""
        publication = await self._ensure_publication(publication_id)
        publication.status = data.status
        await self.db.flush()
        await self.db.refresh(publication)
        return await self._detail_out(publication)

    async def delete_snapshot(
        self, publication_id: str, data: KbPublicationSnapshotDeleteSchema
    ) -> KbPublicationSnapshotDeleteOutSchema:
        """03.4 删除指定快照的正文与向量：委托 ``KbEntryService``，留幂等墓碑。

        维护页发起的删除按契约要求生成高于当前记录值的 ``revision``（``current + 1``）；
        若 ``record_id`` 正是当前生效快照，发布单元同时置为 deleted。
        """
        publication = await self._ensure_publication(publication_id)
        revision_row = await KbRevisionCRUD(self.db).get_by_record(
            publication_id=publication_id, record_id=data.record_id
        )
        if revision_row is None or revision_row.content_hash is None:
            raise CustomException(msg="快照不存在或已删除", status_code=status.HTTP_404_NOT_FOUND)

        next_revision = publication.current_revision + 1
        payload = EntryDeleteData(
            request_id=f"admin-{uuid4().hex}",
            tenant_id=publication.tenant_id or "",
            user_id=ADMIN_USER_ID,
            resource_type=publication.resource_type,
            resource_id=publication.resource_id or "",
            publication_id=publication_id,
            record_id=data.record_id,
            version_id=revision_row.version_id or f"admin-delete-{next_revision}",
            revision=next_revision,
            reason=data.reason,
        )
        receipt = await self._run_entry(KbEntryService().delete(payload))
        return KbPublicationSnapshotDeleteOutSchema(
            publication_id=receipt.publication_id,
            record_id=receipt.record_id,
            revision=receipt.revision,
            deleted=True,
            deleted_chunk_count=receipt.deleted_chunk_count,
            searchable=False,
        )

    async def delete(self, publication_id: str) -> None:
        """整单删除：逻辑删除（``status=deleted``），保留版本与切片供追溯。

        与契约 03.4 同口径：检索与治理范围都只认 ``status=active``，所以逻辑删后
        立刻不可召回。物理清理仍由 ``crud.delete_cascade`` 提供，留给内部脚本（页面
        不提供不可恢复操作）；对已删除的行重复调用是幂等的。
        """
        publication = await self._ensure_publication(publication_id)
        if publication.status == PUBLICATION_DELETED:
            return
        publication.status = PUBLICATION_DELETED
        await self.db.flush()

    # ── 03.1 / 03.3 正文写入与 #20 停用（管理端补录与纠正） ──────────────

    async def write_entry(self, data: KbPublicationEntryWriteSchema) -> KbPublicationEntryWriteOutSchema:
        """写入正文：发布单元不存在则首发（03.1），存在但还没有快照则补录首版。

        「新增发布单元」建出的是空壳（``current_record_id`` 为空），此时不能用
        ``KbEntryService.create``（它见到同 ID 一律 409），而要按「有没有快照」分流：
        已有生效快照属误用，直接 409 提示改用修订。
        """
        return await self._write_entry(
            publication_id=(data.publication_id or f"kb-{uuid4().hex[:12]}").strip(),
            chunks=self._entry_chunks(data.content),
            data=data,
            metadata=self._entry_metadata(
                title=data.title,
                category=data.category,
                summary=data.summary,
                tags=data.tags,
                owner=data.owner,
            ),
            knowledge_no=data.knowledge_no,
        )

    async def write_entry_from_chunks(
        self, data: KbPublicationEntryChunksWriteSchema
    ) -> KbPublicationEntryWriteOutSchema:
        """按给定切片集合写入（03.1 等价）：页面审过的切片原样入库，不重新切分。

        典型调用方是解析任务详情页——产物里的 ``chunks`` 已含人工修订，重切分会
        丢掉修订内容与切片边界，也会让哈希与页面看到的不一致。分流规则与
        :meth:`write_entry` 完全一致（首发 / 空壳补录），差异只在切片来源。
        """
        metadata = dict(data.metadata)
        metadata.update(
            self._entry_metadata(
                title=data.title,
                category=data.category,
                summary=data.summary,
                tags=data.tags,
                owner=data.owner,
            )
        )
        return await self._write_entry(
            publication_id=(data.publication_id or f"kb-{uuid4().hex[:12]}").strip(),
            chunks=self._given_chunks(data.chunks),
            data=data,
            metadata=metadata,
            knowledge_no=data.knowledge_no,
        )

    async def _write_entry(
        self,
        *,
        publication_id: str,
        chunks: tuple[EntryChunkData, ...],
        data: KbPublicationEntryWriteSchema | KbPublicationEntryChunksWriteSchema,
        metadata: dict[str, Any],
        knowledge_no: str | None,
    ) -> KbPublicationEntryWriteOutSchema:
        """两个写入入口共用的分流：无发布单元 → ``create``；有空壳 → ``revise``。"""
        publication = await self.crud.get(publication_id=publication_id)

        if publication is not None and publication.current_record_id is not None:
            raise CustomException(
                msg="该发布单元已有生效快照，请使用「修订正文」写入新版本",
                status_code=status.HTTP_409_CONFLICT,
            )

        if publication is None:
            receipt = await self._run_entry(
                KbEntryService().create(
                    self._entry_write_data(
                        publication_id=publication_id,
                        chunks=chunks,
                        revision=1,
                        base_record_id=None,
                        resource_type=data.resource_type,
                        tenant_id=data.tenant_id,
                        resource_id=data.resource_id,
                        knowledge_no=knowledge_no or publication_id,
                        metadata=metadata,
                    )
                )
            )
            return self._entry_out(receipt, status=PUBLICATION_ACTIVE)

        # 空壳补录：归属字段以库里已登记的值为准，否则 _assert_scope 会把本人操作判成越权
        receipt = await self._run_entry(
            KbEntryService().revise(
                self._entry_write_data(
                    publication_id=publication_id,
                    chunks=chunks,
                    revision=publication.current_revision + 1,
                    base_record_id=None,
                    resource_type=publication.resource_type or data.resource_type,
                    tenant_id=publication.tenant_id or data.tenant_id,
                    resource_id=publication.resource_id or data.resource_id,
                    knowledge_no=knowledge_no or publication_id,
                    metadata=metadata,
                )
            )
        )
        self._sync_display_fields(publication, data, fill_scope=True)
        await self.db.flush()
        await self.db.refresh(publication)
        return self._entry_out(receipt, status=publication.status)

    async def revise_entry(
        self, publication_id: str, data: KbPublicationEntryReviseSchema
    ) -> KbPublicationEntryWriteOutSchema:
        """03.3 修订：整篇重新切分写入新快照，旧快照保留（基线一致才允许）。"""
        publication = await self._ensure_publication(publication_id)
        if publication.current_record_id is None:
            raise CustomException(
                msg="该发布单元还没有已批准快照，请先写入正文", status_code=status.HTTP_409_CONFLICT
            )
        if publication.status != PUBLICATION_ACTIVE:
            raise CustomException(
                msg="发布单元已停用或删除，不能继续修订", status_code=status.HTTP_409_CONFLICT
            )
        if data.base_record_id and data.base_record_id != publication.current_record_id:
            raise CustomException(
                msg=f"基线已变化（当前快照 {publication.current_record_id}），请刷新后重新修订",
                status_code=status.HTTP_409_CONFLICT,
            )

        # 带上旧快照的 metadata：契约字段（trust 等）不在表单里，不能因为管理端改一次就丢
        current = await KbRevisionCRUD(self.db).get_by_record(
            publication_id=publication_id, record_id=publication.current_record_id
        )
        metadata = dict(current.metadata_ or {}) if current is not None else {}
        metadata.update(
            self._entry_metadata(
                title=data.title,
                category=data.category,
                summary=data.summary,
                tags=data.tags,
                owner=data.owner,
            )
        )

        receipt = await self._run_entry(
            KbEntryService().revise(
                self._entry_write_data(
                    publication_id=publication_id,
                    chunks=self._entry_chunks(data.content),
                    revision=publication.current_revision + 1,
                    base_record_id=publication.current_record_id,
                    resource_type=publication.resource_type or "document",
                    tenant_id=publication.tenant_id,
                    resource_id=publication.resource_id,
                    knowledge_no=(current.knowledge_no if current is not None else None) or publication_id,
                    metadata=metadata,
                )
            )
        )
        self._sync_display_fields(publication, data, fill_scope=False)
        await self.db.flush()
        await self.db.refresh(publication)
        return self._entry_out(receipt, status=publication.status)

    async def deactivate(
        self, publication_id: str, data: KbPublicationDeactivateSchema
    ) -> KbPublicationDeactivateOutSchema:
        """#20 停用（D05 同口径）：切片与版本归档、发布单元转 inactive，正文保留供审计。

        与页面「下线」的区别：下线只切 ``status``（可再上线）；停用会把切片与版本行置为
        ``archived``，检索的三处开关同时失效，**不能靠改状态恢复**。
        """
        publication = await self._ensure_publication(publication_id)
        if publication.status == PUBLICATION_DELETED:
            raise CustomException(msg="发布单元已删除，不能停用", status_code=status.HTTP_409_CONFLICT)
        if publication.status == PUBLICATION_INACTIVE:
            raise CustomException(msg="发布单元已停用，无需重复停用", status_code=status.HTTP_409_CONFLICT)
        if publication.current_record_id is None:
            raise CustomException(
                msg="该发布单元还没有已批准快照，不能停用", status_code=status.HTTP_400_BAD_REQUEST
            )

        receipt = await self._run_entry(
            KbEntryService().deactivate(
                EntryDeactivateData(
                    request_id=f"admin-{uuid4().hex}",
                    tenant_id=publication.tenant_id or "",
                    user_id=ADMIN_USER_ID,
                    publication_id=publication_id,
                    record_id=publication.current_record_id,
                    revision=publication.current_revision + 1,
                    reason=data.reason,
                )
            )
        )
        await self.db.refresh(publication)
        return KbPublicationDeactivateOutSchema(
            publication_id=receipt.publication_id,
            record_id=receipt.record_id,
            revision=receipt.revision,
            deactivated=receipt.deactivated,
            searchable=receipt.searchable,
            status=publication.status,
        )

    # ── 私有 ──────────────────────────────────────────────────────────

    @staticmethod
    def _entry_chunks(content: str) -> tuple[EntryChunkData, ...]:
        """整篇正文按解析链路同一口径切分（``PARSE_CHUNK_SIZE`` / ``PARSE_CHUNK_OVERLAP``）。

        切片身份交给 ``KbEntryService`` 按 ``record_id:seq`` 分配，这里不造 chunk_id。
        """
        pieces = split_text(content)
        if not pieces:
            raise CustomException(msg="正文为空，无法写入", status_code=status.HTTP_400_BAD_REQUEST)
        return tuple(
            EntryChunkData(chunk_id=None, seq=index, content=piece.content, heading=piece.meta.get("section_title"))
            for index, piece in enumerate(pieces, start=1)
        )

    @staticmethod
    def _given_chunks(chunks: list[KbPublicationEntryChunkSchema]) -> tuple[EntryChunkData, ...]:
        """页面给定的切片 → 领域切片：按 ``seq`` 升序后重编号 1..N（契约要求连续）。

        不调 ``split_text``：这些切片已经人工审核，重切分会改掉边界与修订内容。
        ``chunk_id`` 交给 ``KbEntryService`` 按 ``record_id:seq`` 确定性分配。
        """
        ordered = sorted(chunks, key=lambda item: item.seq)
        if not ordered:
            raise CustomException(msg="没有可写入的切片", status_code=status.HTTP_400_BAD_REQUEST)
        return tuple(
            EntryChunkData(
                chunk_id=None,
                seq=index,
                content=item.content,
                heading=(item.heading or "").strip() or None,
                page_from=item.page_from,
                page_to=item.page_to,
            )
            for index, item in enumerate(ordered, start=1)
        )

    @staticmethod
    def _entry_metadata(
        *,
        title: str | None,
        category: str | None,
        summary: str | None,
        tags: list[str] | None,
        owner: str | None,
    ) -> dict[str, Any]:
        """表单字段 → 契约 metadata 快照：空值不落键，避免把已有值覆盖成 null。"""
        metadata: dict[str, Any] = {"source": "admin"}
        for name, value in (("title", title), ("category", category), ("summary", summary), ("owner", owner)):
            if value:
                metadata[name] = value
        if tags:
            metadata["tags"] = list(tags)
        return metadata

    @staticmethod
    def _entry_write_data(
        *,
        publication_id: str,
        chunks: tuple[EntryChunkData, ...],
        revision: int,
        base_record_id: str | None,
        resource_type: str,
        tenant_id: str | None,
        resource_id: str | None,
        knowledge_no: str,
        metadata: dict[str, Any],
    ) -> EntryWriteData:
        """组装领域写入载荷：快照 ID 由管理端生成（管理端没有 GSI 的 request/snapshot 体系）。"""
        token = uuid4().hex
        return EntryWriteData(
            request_id=f"admin-{token}",
            tenant_id=tenant_id or "",
            user_id=ADMIN_USER_ID,
            resource_type=resource_type,
            resource_id=resource_id or "",
            publication_id=publication_id,
            record_id=f"{ADMIN_RECORD_PREFIX}{token}",
            version_id=f"admin-v{revision}-{token[:8]}",
            revision=revision,
            knowledge_no=knowledge_no,
            content_hash=canonical_content_hash([asdict(chunk) for chunk in chunks]),
            metadata=metadata,
            chunks=chunks,
            base_record_id=base_record_id,
        )

    @staticmethod
    def _entry_out(receipt: EntryReceipt, *, status: str) -> KbPublicationEntryWriteOutSchema:
        return KbPublicationEntryWriteOutSchema(
            publication_id=receipt.publication_id,
            record_id=receipt.record_id,
            version_id=receipt.version_id,
            revision=receipt.revision,
            content_hash=receipt.content_hash,
            chunk_count=receipt.accepted_chunk_count,
            searchable=receipt.searchable,
            status=status,
        )

    @staticmethod
    def _sync_display_fields(publication: KbPublicationModel, data: Any, *, fill_scope: bool) -> None:
        """表单展示字段回写发布单元行（快照里的 metadata 只服务契约回读，不驱动页面）。"""
        for name in ("title", "category", "summary", "owner"):
            value = getattr(data, name, None)
            if value is not None:
                setattr(publication, name, value)
        tags = getattr(data, "tags", None)
        if tags is not None:
            publication.tags = list(tags)
        if fill_scope:
            tenant_id = getattr(data, "tenant_id", None)
            resource_id = getattr(data, "resource_id", None)
            if publication.tenant_id is None and tenant_id:
                publication.tenant_id = tenant_id
            if publication.resource_id is None and resource_id:
                publication.resource_id = resource_id

    @staticmethod
    async def _run_entry(awaitable: Any) -> Any:
        """领域异常 → HTTP 状态：管理端只区分语义，不复刻契约错误码。"""
        try:
            return await awaitable
        except EntryConflictError as exc:
            raise CustomException(msg=str(exc), status_code=status.HTTP_409_CONFLICT) from exc
        except EntryNotFoundError as exc:
            raise CustomException(msg=str(exc), status_code=status.HTTP_404_NOT_FOUND) from exc
        except EntryScopeError as exc:
            raise CustomException(msg=str(exc), status_code=status.HTTP_403_FORBIDDEN) from exc
        except EntryEmbeddingError as exc:
            raise CustomException(msg=str(exc), status_code=status.HTTP_500_INTERNAL_SERVER_ERROR) from exc

    @staticmethod
    def _parse_cursor(cursor: str | None) -> int:
        """03.2 游标：契约里是不透明字符串，本侧按 ``seq`` 实现；非法游标 400。"""
        if cursor is None:
            return 0
        try:
            value = int(cursor)
        except (TypeError, ValueError) as exc:
            raise CustomException(msg="cursor 无效：请原样回传上一页的 next_cursor") from exc
        if value < 0:
            raise CustomException(msg="cursor 无效：请原样回传上一页的 next_cursor")
        return value

    async def _ensure_publication(self, publication_id: str) -> KbPublicationModel:
        """校验发布单元存在，否则 404（详情/编辑/删除/版本/切片共用）。"""
        publication = await KbPublicationCRUD(self.db).get(publication_id=publication_id)
        if publication is None:
            raise CustomException(msg="发布单元不存在", status_code=status.HTTP_404_NOT_FOUND)
        return publication
