"""知识写入服务（#03 / #05 / #06 的业务实现）：发布单元、版本快照与切片的原子落库。

分工与口径：
1. 契约校验（切片集合 / content_hash / 字段）在 ``app/contract/entries.py``；本层只做业务状态机与落库；
2. **向量化先于事务**：embedding 失败时不产生任何半成品行，避免「有正文没向量、检索永远召回不到」；
3. 写入是「全量替换 + 指针切换」：新快照整体写入，旧快照保留（#04 需按 record_id 精读），
   检索只认 ``publication.current_record_id/current_revision``；
4. 删除是物理删切片 + 版本墓碑（行保留、`status='archived'` + `searchable=False`）：旧请求带旧
   revision 重放会被 ``current_revision`` 单调性挡住，无法复活内容；墓碑行本身要能被查到
   （客户端拿破回执里的 version_id 回查应看到「已删除」而不是 404），所以不做隐藏；
5. 并发冲突用领域异常表达，契约层再映射成 409 / 404 / 403，service 不依赖 HTTP 契约；
6. **并发安全靠数据库而不是「先读后写」**：指针推进一律走 ``KbPublicationCRUD.advance``
   （以读到的 `current_revision` 作 CAS 条件），首次发布靠 `publication_id` 唯一键兜底；
7. **契约字段全部落库**（P0-10）：``tenant_id`` / ``resource_id`` 进 publication、
   ``version_id`` / ``knowledge_no`` / ``metadata`` 进 revision、``chunk_id`` 进 chunk。
   在此之前这些值只出现在回执里、落库时被丢弃，导致 #04 无法按版本/分片精读，
   归属校验也只能比 ``resource_type``（拿到 publication_id 即可改写他人知识）。
"""

import hashlib
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.exc import IntegrityError

from app.core.clients import get_embedding_client
from app.core.database import async_session_factory
from app.core.exceptions import CustomException
from app.core.logger import logger
from app.core.rag.vector_store import validate_embedding

from ..chunk.crud import KbChunkCRUD
from ..revision.crud import KbRevisionCRUD
from .crud import KbPublicationCRUD
from .model import KbPublicationModel
from .schema import EntryChunkData, EntryDeleteData, EntryDeleteReceipt, EntryReceipt, EntryWriteData

PUBLICATION_ACTIVE = "active"
PUBLICATION_DELETED = "deleted"
REVISION_ONLINE = "online"
REVISION_ARCHIVED = "archived"


class EntryConflictError(Exception):
    """业务冲突：publication 已存在、revision 过期、基线被他人修改。"""


class EntryNotFoundError(Exception):
    """目标发布单元不存在。"""


class EntryScopeError(Exception):
    """发布单元归属与本次请求声明的资源不一致（越权）。"""


class EntryEmbeddingError(Exception):
    """向量化或向量写入依赖不可用。"""


class KbEntryService:
    """知识写入服务：首发、修订、整单删除。"""

    async def create(self, data: EntryWriteData) -> EntryReceipt:
        """#03 首次发布：publication_id 已存在一律 409，绝不隐式覆盖。"""
        rows = await self._build_chunk_rows(data)
        try:
            async with async_session_factory() as session, session.begin():
                pub_crud = KbPublicationCRUD(session)
                if await pub_crud.get(publication_id=data.publication_id) is not None:
                    raise EntryConflictError(f"publication_id {data.publication_id} 已存在，首次发布不允许隐式覆盖。")

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

    async def revise(self, data: EntryWriteData) -> EntryReceipt:
        """#05 修订：base_record_id 与当前基线不一致 / revision 不递增均视为 409。"""
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
        """#06 整单删除：物理删指定快照切片，版本行留墓碑，publication 标记 deleted。

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

    @staticmethod
    def _assert_scope(publication: KbPublicationModel, data: EntryWriteData | EntryDeleteData) -> None:
        """发布单元归属校验：不允许拿别人的 publication_id 改写内容（P0-10）。

        校验三元组 ``tenant_id + resource_type + resource_id``：
        - ``resource_type`` 拦「把文档当 FAQ 改」这类跨域误用；
        - ``resource_id`` 拦「拿到某 publication_id 就能改任意资源」的越权；
        - ``tenant_id`` 拦跨租户越权（多租户上线后这一条才是真正起作用的那道）。

        存量行（本次加列之前写入）归属列为 NULL，此时只降级校验 ``resource_type``——
        这类行没有归属信息可比，硬拒会让旧数据无法修订。新写入的行三列都必填。
        """
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
