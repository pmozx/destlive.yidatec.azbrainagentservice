"""契约写入链路（03.1 / 03.2 / 03.3 / 03.4）：切片向量化 → 事务内落库 → 指针 CAS 切换。

为什么与 ``service.py`` 分开：

1. 调用方不同：本类只被 ``modules_api/kb/knowledge/service.py`` 使用，不进管理端路由；
   事务边界也不同——这里自持 ``async_session_factory``，不接收 ``self.db``；
2. 领域异常（``EntryConflictError`` 等）定义在本模块，契约层映射错误码、管理端映射 HTTP
   状态，各自持有自己的映射表；
3. 两个类合居一个文件会到 1100 行，改管理端逻辑时要跨过整段契约写入代码。

写入口径：向量化先于事务（失败不产生半成品行）；写入是「全量替换 + 指针切换」（旧快照
保留，检索只认当前指针）；并发安全靠 ``KbPublicationCRUD.advance`` 的 CAS + 唯一键兜底。
"""

import hashlib
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.exc import IntegrityError

from app.core.clients import get_embedding_client
from app.core.database import async_session_factory
from app.core.exceptions import CustomException
from app.core.logger import logger
from app.core.rag.chunker import embedding_text
from app.core.rag.vector_store import validate_embedding

from ..chunk.crud import KbChunkCRUD
from ..revision.crud import KbRevisionCRUD
from ..revision.model import REVISION_ONLINE
from .crud import KbPublicationCRUD
from .model import KbPublicationModel
from .schema import (
    BaselineChunk,
    BaselineData,
    BaselineQuery,
    EntryChunkData,
    EntryDeactivateData,
    EntryDeactivateReceipt,
    EntryDeleteData,
    EntryDeleteReceipt,
    EntryReceipt,
    EntryWriteData,
    PUBLICATION_ACTIVE,
    PUBLICATION_DELETED,
    PUBLICATION_INACTIVE,
)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 异常定义（契约层与管理端各自映射成自己的错误体）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class EntryConflictError(Exception):
    """业务冲突：publication 已存在、revision 过期、基线被他人修改。"""


class EntryNotFoundError(Exception):
    """目标发布单元不存在。"""


class EntryScopeError(Exception):
    """发布单元归属与本次请求声明的资源不一致（越权）。"""


class EntryEmbeddingError(Exception):
    """向量化或向量写入依赖不可用。"""


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
            data.publication_id, data.record_id, data.revision, len(rows),
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
            data.publication_id, data.record_id, data.revision, len(rows),
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
            data.publication_id, data.record_id, data.revision, deleted,
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

    async def deactivate(self, data: EntryDeactivateData) -> EntryDeactivateReceipt:
        """D05 停用已生效知识：停止检索与引用，**保留**正文与向量供审计。

        与 03.4 删除的区别只在「留不留内容」：删除物理清除切片（#06 要求删后不可召回），
        停用只把切片与版本置为 `archived` 并让发布单元离开 `active`。三处同时失效是刻意冗余：
        检索硬过滤同时看「发布单元生效」「快照可检索」「切片在线」，任何一处漏改都会在
        停用后继续被召回。

        幂等口径：完全相同（record_id 与 revision 都一致）的重复调用直接返回同一份回执；
        已停用后拿旧修订再来，一律按 409 `STALE_REVISION` 拒绝，而不是“静默成功”。
        """
        async with async_session_factory() as session, session.begin():
            pub_crud = KbPublicationCRUD(session)
            publication = await pub_crud.get(publication_id=data.publication_id)
            if publication is None:
                raise EntryNotFoundError(f"发布单元 {data.publication_id} 不存在。")
            # D05 没有 resource_type / resource_id：只校验租户归属
            if publication.tenant_id is not None and publication.tenant_id != data.tenant_id:
                raise EntryScopeError(
                    f"发布单元 {data.publication_id} 属于租户 {publication.tenant_id}，与请求的 {data.tenant_id} 不一致。"
                )
            if publication.status == PUBLICATION_DELETED:
                raise EntryConflictError(f"发布单元 {data.publication_id} 已删除，不能停用。")
            if publication.status == PUBLICATION_INACTIVE:
                if publication.current_record_id == data.record_id and publication.current_revision == data.revision:
                    return self._deactivate_receipt(data)
                raise EntryConflictError(
                    f"发布单元 {data.publication_id} 已停用（revision={publication.current_revision}），"
                    "不同修订的停用请求请以最新基线为准。"
                )
            if publication.current_record_id != data.record_id:
                raise EntryConflictError(
                    f"record_id {data.record_id} 不是当前生效快照（当前为 {publication.current_record_id}），请读取最新基线。"
                )
            if data.revision <= publication.current_revision:
                raise EntryConflictError(
                    f"停用 revision 必须大于当前值 {publication.current_revision}，收到 {data.revision}。"
                )

            now = datetime.now(UTC)
            archived_chunks = await KbChunkCRUD(session).archive_by_record(
                publication_id=data.publication_id, record_id=data.record_id
            )
            await KbRevisionCRUD(session).archive_by_record(
                publication_id=data.publication_id, record_id=data.record_id
            )
            # current_record_id 保留：停用后仍需知道「停的是哪个快照」（审计与幂等判定都要它），
            # 召回侧由 status != active 拦住，不依赖指针是否为空
            advanced = await pub_crud.advance(
                publication_id=data.publication_id,
                expected_revision=publication.current_revision,
                values={"status": PUBLICATION_INACTIVE, "current_revision": data.revision, "last_synced_at": now},
            )
            if not advanced:
                raise EntryConflictError(
                    f"发布单元 {data.publication_id} 的基线已被并发请求推进，请读取最新内容后重新提交。"
                )

        logger.info(
            "知识停用完成：publication_id={} record_id={} revision={} archived_chunks={} reason={}",
            data.publication_id, data.record_id, data.revision, archived_chunks, data.reason,
        )
        return self._deactivate_receipt(data)

    # ── 私有方法 ────────────────────────────────────────────────────────────

    @staticmethod
    def _deactivate_receipt(data: EntryDeactivateData) -> EntryDeactivateReceipt:
        return EntryDeactivateReceipt(
            request_id=data.request_id,
            publication_id=data.publication_id,
            record_id=data.record_id,
            revision=data.revision,
        )

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
        """切片 → 向量 → 行；向量条数不足/维度不符直接失败，不入库。

        ``chunk_id`` 缺省时按 ``record_id:seq`` 确定性分配：规则必须确定性（同请求重试不能
        换 ID），与 03.2 示例 ``record-demo-001:1`` 一致。分配属于领域（切片身份），
        不放契约层——否则只有 ``/v1`` 一个入口有 ID 规则。
        """
        chunks: tuple[EntryChunkData, ...] = data.chunks
        vectors = await self._embed(chunks)
        if len(vectors) != len(chunks):
            raise EntryEmbeddingError("向量化返回条数与切片条数不一致，本次写入已拒绝。")
        return [
            {
                "publication_id": data.publication_id,
                "record_id": data.record_id,
                "revision": data.revision,
                "chunk_id": chunk.chunk_id or f"{data.record_id}:{chunk.seq}",
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
        """批量向量化（分批并发 + 重试由客户端负责）；任何依赖异常都归口为可重试失败。

        送入模型的是「标题路径 + 正文」（与解析链路同一个拼接口径，见
        :func:`app.core.rag.chunker.embedding_text`）；落库与检索返回的正文仍是原文。
        """
        try:
            # 进程级单例：每请求 new 一个会让 httpx 连接池既不复用也永不关闭
            vectors = await get_embedding_client().embed_texts(
                [embedding_text(chunk.content, chunk.heading) for chunk in chunks]
            )
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
