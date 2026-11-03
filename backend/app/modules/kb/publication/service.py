"""发布单元服务：契约写入 + 管理端维护，统一在同一个模块。

合并原 ``knowledge`` 子模块后，本文件包含两个服务类：

- **KbEntryService**：契约写入链路（03.1 首发 / 03.2 基线读取 / 03.3 修订 / 03.4 删除），
  由 ``modules_api/contract/knowledge/service.py`` 调用；
- **KbPublicationService**：管理端维护链路（列表 / 详情 / 新增 / 编辑 / 状态 / 删除 / 版本 / 切片），
  由 ``modules/kb/publication/controller.py`` 调用。

设计要点：
1. 契约校验在 ``app/modules_api/contract/knowledge/service.py``；本层只做业务状态机与落库；
2. **向量化先于事务**：embedding 失败时不产生任何半成品行；
3. 写入是「全量替换 + 指针切换」：新快照整体写入，旧快照保留，检索只认当前指针；
4. 并发安全靠数据库 CAS（``KbPublicationCRUD.advance``）+ 唯一键兜底；
5. 契约字段全部落库：``tenant_id`` / ``resource_id`` 进 publication、``version_id`` / ``knowledge_no`` /
   ``metadata`` 进 revision、``chunk_id`` 进 chunk。
"""

import hashlib
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_schema import PageResultSchema
from app.core.clients import get_embedding_client
from app.core.database import async_session_factory
from app.core.exceptions import CustomException
from app.core.logger import logger
from app.core.rag.vector_store import validate_embedding
from app.utils.common_util import search_to_dict

from ..chunk.crud import KbChunkCRUD
from ..revision.crud import KbRevisionCRUD
from .crud import KbPublicationCRUD
from .model import KbPublicationModel
from .schema import (
    BaselineChunk,
    BaselineData,
    BaselineQuery,
    EntryChunkData,
    EntryDeleteData,
    EntryDeleteReceipt,
    EntryReceipt,
    EntryWriteData,
    KbPublicationBaselineChunkOutSchema,
    KbPublicationBaselineOutSchema,
    KbPublicationChunkDetailOutSchema,
    KbPublicationChunkOutSchema,
    KbPublicationCreateSchema,
    KbPublicationDetailOutSchema,
    KbPublicationOutSchema,
    KbPublicationQueryParam,
    KbPublicationRevisionDetailOutSchema,
    KbPublicationRevisionOutSchema,
    KbPublicationSnapshotDeleteOutSchema,
    KbPublicationSnapshotDeleteSchema,
    KbPublicationStatusSchema,
    KbPublicationUpdateSchema,
)

# ── 状态常量 ────────────────────────────────────────────────────────────────
PUBLICATION_ACTIVE = "active"
PUBLICATION_DELETED = "deleted"
REVISION_ONLINE = "online"
REVISION_ARCHIVED = "archived"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 异常定义（契约层会映射为 HTTP 状态码）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class EntryConflictError(Exception):
    """业务冲突：publication 已存在、revision 过期、基线被他人修改。"""


class EntryNotFoundError(Exception):
    """目标发布单元不存在。"""


class EntryScopeError(Exception):
    """发布单元归属与本次请求声明的资源不一致（越权）。"""


class EntryEmbeddingError(Exception):
    """向量化或向量写入依赖不可用。"""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# §一  契约写入服务（03.1 / 03.2 / 03.3 / 03.4）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class KbEntryService:
    """知识写入服务：新增、读取基线、修改、删除。"""

    async def create(self, data: EntryWriteData) -> EntryReceipt:
        """03.1 新增已批准切片集合：publication_id 已存在一律 409，绝不隐式覆盖。"""
        rows = await self._build_chunk_rows(data)
        try:
            async with async_session_factory() as session, session.begin():
                pub_crud = KbPublicationCRUD(session)
                if await pub_crud.get(publication_id=data.publication_id) is not None:
                    raise EntryConflictError(f"publication_id {data.publication_id} 已存在，首次发布不允许隐式覆盖。")
                await self._assert_chunk_ownership(session, data)

                now = datetime.now(UTC)
                await KbRevisionCRUD(session).create(
                    data={
                        "publication_id": data.publication_id,
                        "record_id": data.record_id,
                        "version_id": data.version_id,
                        "knowledge_no": data.knowledge_no,
                        "metadata_": data.metadata or None,
                        "base_record_id": None,
                        "revision": data.revision,
                        "content_hash": self._stored_hash(data.content_hash),
                        "chunk_count": len(rows),
                        "searchable": bool(rows),
                        "status": REVISION_ONLINE,
                    }
                )
                await KbChunkCRUD(session).insert_many(rows)
                await pub_crud.create(
                    data={
                        "publication_id": data.publication_id,
                        "tenant_id": data.tenant_id,
                        "resource_id": data.resource_id,
                        "resource_type": data.resource_type,
                        "current_record_id": data.record_id,
                        "current_revision": data.revision,
                        "last_synced_at": now,
                        "status": PUBLICATION_ACTIVE,
                        # 管理端展示字段：从契约载荷派生默认值，管理端后续可编辑
                        "title": data.metadata.get("title") or data.knowledge_no or data.publication_id,
                        "category": data.metadata.get("category"),
                        "summary": data.metadata.get("summary"),
                        "tags": data.metadata.get("tags", []),
                        "owner": data.metadata.get("owner"),
                    }
                )
        except IntegrityError as exc:
            # 「先查再插」在并发下会双双通过校验，真正的互斥来自唯一键；
            # 这里翻成与串行场景一致的 409，而不是让唯一约束错误冒成 500
            raise EntryConflictError(f"publication_id {data.publication_id} 已存在，首次发布不允许隐式覆盖。") from exc

        logger.info(
            "知识首发写入完成：publication_id={} record_id={} revision={} chunks={}",
            data.publication_id,
            data.record_id,
            data.revision,
            len(rows),
        )
        return self._receipt(data, accepted_chunk_count=len(rows))

    async def read_baseline(self, data: BaselineQuery) -> BaselineData:
        """03.2 读取知识修订基线：只读指定 ``record_id`` 的已批准快照，按 ``seq`` 游标分页。"""
        async with async_session_factory() as session:
            publication = await KbPublicationCRUD(session).get(publication_id=data.publication_id)
            if publication is None:
                raise EntryNotFoundError(f"发布单元 {data.publication_id} 不存在。")
            # 读取接口没有 resource_id，只能校验租户归属；存量行 tenant_id 为 NULL 时降级放行
            if publication.tenant_id is not None and publication.tenant_id != data.tenant_id:
                raise EntryScopeError(
                    f"发布单元 {data.publication_id} 属于租户 {publication.tenant_id}，与请求的 {data.tenant_id} 不一致。"
                )

            revision = await KbRevisionCRUD(session).get_by_record(
                publication_id=data.publication_id, record_id=data.record_id
            )
            if revision is None or revision.content_hash is None:
                # 无版本行或删除墓碑：正文与向量已物理删除，不能再返回可读快照
                raise EntryNotFoundError(f"快照 {data.record_id} 不存在或已删除。")

            rows = await KbChunkCRUD(session).page_by_record(
                publication_id=data.publication_id,
                record_id=data.record_id,
                after_seq=data.after_seq,
                limit=data.limit,
            )
            has_more = len(rows) > data.limit
            rows = rows[: data.limit]

            chunks = tuple(
                BaselineChunk(
                    chunk_id=row.chunk_id or f"{row.record_id}:{row.seq}",
                    seq=row.seq,
                    content=row.content,
                    heading=row.heading,
                    page_from=row.page_from,
                    page_to=row.page_to,
                )
                for row in rows
            )
            next_cursor = str(chunks[-1].seq) if has_more and chunks else None
            return BaselineData(
                publication_id=publication.publication_id,
                resource_type=publication.resource_type,
                resource_id=publication.resource_id or "",
                record_id=revision.record_id,
                version_id=revision.version_id or "",
                revision=revision.revision,
                knowledge_no=revision.knowledge_no or "",
                metadata=revision.metadata_ or {},
                # 落库时去掉 ``sha256:`` 前缀（varchar(64)），对外恢复契约值
                content_hash=f"sha256:{revision.content_hash}",
                chunks=chunks,
                searchable=revision.searchable,
                next_cursor=next_cursor,
            )

    async def revise(self, data: EntryWriteData) -> EntryReceipt:
        """03.3 修改：base_record_id 与当前基线不一致 / revision 不递增均视为 409。"""
        rows = await self._build_chunk_rows(data)
        async with async_session_factory() as session, session.begin():
            pub_crud = KbPublicationCRUD(session)
            publication = await pub_crud.get(publication_id=data.publication_id)
            if publication is None:
                raise EntryNotFoundError(f"发布单元 {data.publication_id} 不存在。")
            self._assert_scope(publication, data)
            if publication.status != PUBLICATION_ACTIVE:
                raise EntryConflictError(f"发布单元 {data.publication_id} 已删除或停用，不能继续修订。")
            if publication.current_record_id != data.base_record_id:
                raise EntryConflictError("base_record_id 与当前基线不一致，请读取最新内容后重新提交。")
            if data.revision <= publication.current_revision:
                raise EntryConflictError(
                    f"revision 必须大于当前值 {publication.current_revision}，收到 {data.revision}。"
                )
            if data.record_id == publication.current_record_id:
                raise EntryConflictError("record_id 必须是新快照，不能复用当前生效快照。")
            if await KbRevisionCRUD(session).record_exists(
                publication_id=data.publication_id, record_id=data.record_id
            ):
                raise EntryConflictError(f"record_id {data.record_id} 已在该发布单元使用过，不能复用历史快照。")
            await self._assert_chunk_ownership(session, data)

            now = datetime.now(UTC)
            await KbRevisionCRUD(session).create(
                data={
                    "publication_id": data.publication_id,
                    "record_id": data.record_id,
                    "version_id": data.version_id,
                    "knowledge_no": data.knowledge_no,
                    "metadata_": data.metadata or None,
                    "base_record_id": data.base_record_id,
                    "revision": data.revision,
                    "content_hash": self._stored_hash(data.content_hash),
                    "chunk_count": len(rows),
                    "searchable": bool(rows),
                    "status": REVISION_ONLINE,
                }
            )
            await KbChunkCRUD(session).insert_many(rows)
            advanced = await pub_crud.advance(
                publication_id=data.publication_id,
                expected_revision=publication.current_revision,
                values={
                    "current_record_id": data.record_id,
                    "current_revision": data.revision,
                    "last_synced_at": now,
                },
            )
            if not advanced:
                # 校验通过到写入之间指针被别人推进了：整笔事务回滚，交调用方按基线过期重试
                raise EntryConflictError(
                    f"发布单元 {data.publication_id} 的基线已被并发修订推进，请读取最新内容后重新提交。"
                )

        logger.info(
            "知识修订写入完成：publication_id={} record_id={} revision={} chunks={}",
            data.publication_id,
            data.record_id,
            data.revision,
            len(rows),
        )
        return self._receipt(data, accepted_chunk_count=len(rows))

    async def delete(self, data: EntryDeleteData) -> EntryDeleteReceipt:
        """03.4 删除已批准切片集合：物理删指定快照切片，版本行留墓碑，publication 标记 deleted。

        手册 §六.03.4 的语义是「删除指定 ``record_id`` 的正文和向量」，只有「与较新修订冲突」
        才 409。所以这里只做 revision 单调校验：删的是当前基线就顺带把发布单元置为 deleted，
        删的是历史快照则保留当前指针，仅推进 ``current_revision`` 防止旧请求复活。
        """
        async with async_session_factory() as session, session.begin():
            pub_crud = KbPublicationCRUD(session)
            publication = await pub_crud.get(publication_id=data.publication_id)
            if publication is None:
                raise EntryNotFoundError(f"发布单元 {data.publication_id} 不存在。")
            self._assert_scope(publication, data)
            if data.revision <= publication.current_revision:
                raise EntryConflictError(
                    f"删除 revision 必须大于当前值 {publication.current_revision}，收到 {data.revision}。"
                )

            now = datetime.now(UTC)
            deleted = await KbChunkCRUD(session).delete_by_record(
                publication_id=data.publication_id, record_id=data.record_id
            )
            await KbRevisionCRUD(session).archive_by_record(
                publication_id=data.publication_id, record_id=data.record_id
            )
            await KbRevisionCRUD(session).create_tombstone(
                publication_id=data.publication_id,
                record_id=data.record_id,
                base_record_id=publication.current_record_id,
                revision=data.revision,
                version_id=data.version_id,
            )
            changes: dict[str, Any] = {"current_revision": data.revision, "last_synced_at": now}
            if data.record_id == publication.current_record_id:
                # 删掉的是当前生效快照：发布单元不再有可检索内容
                changes.update({"status": PUBLICATION_DELETED, "current_record_id": None})
            advanced = await pub_crud.advance(
                publication_id=data.publication_id,
                expected_revision=publication.current_revision,
                values=changes,
            )
            if not advanced:
                # 与 revise 同口径：并发下要么删除要么修订，先落地的赢，后到的按基线过期处理
                raise EntryConflictError(
                    f"发布单元 {data.publication_id} 的基线已被并发请求推进，请读取最新内容后重新提交。"
                )

        logger.info(
            "知识删除完成：publication_id={} record_id={} revision={} deleted_chunks={}",
            data.publication_id,
            data.record_id,
            data.revision,
            deleted,
        )
        return EntryDeleteReceipt(
            request_id=data.request_id,
            resource_type=data.resource_type,
            resource_id=data.resource_id,
            publication_id=data.publication_id,
            record_id=data.record_id,
            version_id=data.version_id,
            revision=data.revision,
            deleted_chunk_count=deleted,
        )

    # ── 私有方法 ────────────────────────────────────────────────────────────

    @staticmethod
    def _assert_scope(publication: KbPublicationModel, data: EntryWriteData | EntryDeleteData) -> None:
        """发布单元归属校验：不允许拿别人的 publication_id 改写内容（P0-10）。"""
        if publication.resource_type != data.resource_type:
            raise EntryScopeError(
                f"发布单元 {data.publication_id} 属于 {publication.resource_type}，与请求的 {data.resource_type} 不一致。"
            )
        if publication.tenant_id is not None and publication.tenant_id != data.tenant_id:
            raise EntryScopeError(
                f"发布单元 {data.publication_id} 属于租户 {publication.tenant_id}，与请求的 {data.tenant_id} 不一致。"
            )
        if publication.resource_id is not None and publication.resource_id != data.resource_id:
            raise EntryScopeError(
                f"发布单元 {data.publication_id} 归属资源 {publication.resource_id}，与请求的 {data.resource_id} 不一致。"
            )

    @staticmethod
    async def _assert_chunk_ownership(session: Any, data: EntryWriteData) -> None:
        """切片 ID 归属校验：``chunk_id`` 不能被跨发布单元复用（03.1 / 03.3）。"""
        provided = [chunk.chunk_id for chunk in data.chunks if chunk.chunk_id]
        if not provided:
            return
        foreign = await KbChunkCRUD(session).fetch_foreign_ids(
            publication_id=data.publication_id, chunk_ids=provided
        )
        if foreign:
            raise EntryScopeError(f"切片 {sorted(foreign)} 属于其它发布单元，不能引用他人的 chunk_id。")

    async def _build_chunk_rows(self, data: EntryWriteData) -> list[dict[str, Any]]:
        """切片 → 向量 → 行；向量条数不足/维度不符直接失败，不入库。"""
        chunks: tuple[EntryChunkData, ...] = data.chunks
        vectors = await self._embed(chunks)
        if len(vectors) != len(chunks):
            raise EntryEmbeddingError("向量化返回条数与切片条数不一致，本次写入已拒绝。")
        return [
            {
                "publication_id": data.publication_id,
                "record_id": data.record_id,
                "revision": data.revision,
                "chunk_id": chunk.chunk_id,
                "seq": chunk.seq,
                "heading": chunk.heading,
                "content": chunk.content,
                "page_from": chunk.page_from,
                "page_to": chunk.page_to,
                "source_type": "text",
                "content_hash": hashlib.sha256(chunk.content.encode("utf-8")).hexdigest(),
                "embedding": vector,
                "status": REVISION_ONLINE,
            }
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]

    @staticmethod
    async def _embed(chunks: tuple[EntryChunkData, ...]) -> list[list[float]]:
        """批量向量化（分批并发 + 重试由客户端负责）；任何依赖异常都归口为可重试失败。"""
        try:
            # 进程级单例：每请求 new 一个会让 httpx 连接池既不复用也永不关闭
            vectors = await get_embedding_client().embed_texts([chunk.content for chunk in chunks])
            return [validate_embedding(vector) for vector in vectors]
        except Exception as exc:
            detail = exc.msg if isinstance(exc, CustomException) else str(exc)
            logger.warning("知识写入向量化失败：{}", detail)
            raise EntryEmbeddingError("向量化依赖暂不可用。") from exc

    @staticmethod
    def _stored_hash(content_hash: str) -> str:
        """落库摘要：契约值是 ``sha256:<hex>``（71 字符），而 ``sbp_ai_kb_revision.content_hash``
        按 §6.5 是 varchar(64)，所以只存算法后的摘要部分（回执仍原样返回契约值）。"""
        return content_hash.rsplit(":", 1)[-1][:64]

    @staticmethod
    def _receipt(data: EntryWriteData, *, accepted_chunk_count: int) -> EntryReceipt:
        return EntryReceipt(
            request_id=data.request_id,
            resource_type=data.resource_type,
            resource_id=data.resource_id,
            publication_id=data.publication_id,
            record_id=data.record_id,
            version_id=data.version_id,
            revision=data.revision,
            content_hash=data.content_hash,
            accepted_chunk_count=accepted_chunk_count,
            searchable=accepted_chunk_count > 0,
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# §二  管理端维护服务（列表 / 详情 / 新增 / 编辑 / 状态 / 删除 / 版本 / 切片）
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
        publication = await self._ensure_publication(publication_id)
        return KbPublicationDetailOutSchema(
            **self._to_out_dict(publication),
            version_count=await self.crud.count_versions(publication_id),
            chunk_count=await self.crud.count_chunks(publication_id),
        )

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

        return KbPublicationBaselineOutSchema(
            publication_id=baseline.publication_id,
            resource_type=baseline.resource_type,
            resource_id=baseline.resource_id,
            record_id=baseline.record_id,
            version_id=baseline.version_id,
            revision=baseline.revision,
            knowledge_no=baseline.knowledge_no,
            metadata=baseline.metadata,
            content_hash=baseline.content_hash,
            chunks=[
                KbPublicationBaselineChunkOutSchema(
                    chunk_id=chunk.chunk_id,
                    seq=chunk.seq,
                    heading=chunk.heading,
                    content=chunk.content,
                    page_from=chunk.page_from,
                    page_to=chunk.page_to,
                )
                for chunk in baseline.chunks
            ],
            searchable=baseline.searchable,
            next_cursor=baseline.next_cursor,
        )

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
        values = data.model_dump(exclude_unset=True)
        # tags 需要特殊处理：Pydantic 的 exclude_unset 对空列表也保留，但空列表是合法值
        if "tags" in values and values["tags"] is not None:
            publication.tags = values.pop("tags")

        for key, value in values.items():
            if hasattr(publication, key):
                setattr(publication, key, value)
        await self.db.flush()
        await self.db.refresh(publication)

        return KbPublicationDetailOutSchema(
            **self._to_out_dict(publication),
            version_count=await self.crud.count_versions(publication_id),
            chunk_count=await self.crud.count_chunks(publication_id),
        )

    async def update_status(self, publication_id: str, data: KbPublicationStatusSchema) -> KbPublicationDetailOutSchema:
        """上下线：只改发布单元状态（active/inactive/deleted），不动内容与版本。"""
        publication = await self._ensure_publication(publication_id)
        publication.status = data.status
        await self.db.flush()
        await self.db.refresh(publication)
        return KbPublicationDetailOutSchema(
            **self._to_out_dict(publication),
            version_count=await self.crud.count_versions(publication_id),
            chunk_count=await self.crud.count_chunks(publication_id),
        )

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
            user_id="admin",
            resource_type=publication.resource_type,
            resource_id=publication.resource_id or "",
            publication_id=publication_id,
            record_id=data.record_id,
            version_id=revision_row.version_id or f"admin-delete-{next_revision}",
            revision=next_revision,
            reason=data.reason,
        )
        try:
            receipt = await KbEntryService().delete(payload)
        except EntryConflictError as exc:
            raise CustomException(msg=str(exc), status_code=status.HTTP_409_CONFLICT) from exc
        except EntryNotFoundError as exc:
            raise CustomException(msg=str(exc), status_code=status.HTTP_404_NOT_FOUND) from exc
        except EntryScopeError as exc:
            raise CustomException(msg=str(exc), status_code=status.HTTP_403_FORBIDDEN) from exc
        except EntryEmbeddingError as exc:
            raise CustomException(msg=str(exc), status_code=status.HTTP_500_INTERNAL_SERVER_ERROR) from exc
        return KbPublicationSnapshotDeleteOutSchema(
            publication_id=receipt.publication_id,
            record_id=receipt.record_id,
            revision=receipt.revision,
            deleted=True,
            searchable=False,
        )

    async def delete(self, publication_id: str) -> None:
        """整单删除：物理删除切片、版本与发布单元（不可恢复）。"""
        await self._ensure_publication(publication_id)
        await self.crud.delete_cascade(publication_id=publication_id)

    # ── 私有 ──────────────────────────────────────────────────────────

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

    @staticmethod
    def _to_out_dict(publication: KbPublicationModel) -> dict[str, Any]:
        """将发布单元模型转换为响应字段字典。"""
        return {
            "id": publication.id,
            "uuid": publication.uuid,
            "created_time": publication.created_time,
            "updated_time": publication.updated_time,
            "publication_id": publication.publication_id,
            "title": publication.title,
            "category": publication.category,
            "summary": publication.summary,
            "tags": publication.tags or [],
            "owner": publication.owner,
            "resource_type": publication.resource_type,
            "tenant_id": publication.tenant_id,
            "resource_id": publication.resource_id,
            "current_record_id": publication.current_record_id,
            "current_revision": publication.current_revision,
            "last_synced_at": publication.last_synced_at,
            "status": publication.status,
        }
