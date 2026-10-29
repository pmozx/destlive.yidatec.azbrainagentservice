"""知识检索门面：AI 问答域（``integration_chat``）读知识库的唯一入口。

依赖方向是**单向**的：chat → kb。本文件是这条依赖的唯一接缝，
问答域不需要（也不允许）直接 import 知识库的 model / crud / service：

- 检索：向量召回 + 「文档已上架、未删除」过滤，回带引用所需的元数据；
- 引用计数：问答产出引用后累加 ``ref_count``，供「文档引用排行」使用。

为什么在应用侧过滤而不是 SQL join：向量召回必须走 HNSW 索引（``<=>`` 排序 + limit），
join 文档表会把索引扫描结果再筛一遍；召回窗口内过滤的代价更小，
下架文档本来就不该出现在窗口里，若窗口被占满属于「该给检索调参」，不是过滤逻辑的问题。
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import logger

from .category.model import IntegrationKbCategoryModel
from .chunk.vector_store import search_chunks
from .document.model import DocumentStatus, IntegrationKbDocumentModel
from .space.model import IntegrationKbSpaceModel


@dataclass(frozen=True)
class KnowledgeHit:
    """一条知识命中（分片原文 + 文档元数据，够拼上下文与引用卡片）"""

    chunk_id: int
    space_id: int
    doc_id: int
    chunk_index: int
    page_no: int | None
    section_title: str | None
    content: str
    score: float
    doc_title: str
    file_name: str
    file_type: str
    space_name: str
    category_name: str
    meta: dict[str, Any]


async def search_knowledge(
    db: AsyncSession,
    query_embedding: Sequence[float],
    *,
    space_ids: Sequence[int] | None = None,
    top_k: int,
    min_score: float | None = None,
) -> list[KnowledgeHit]:
    """按问题向量召回知识分片（只回「已上架且解析完成」的文档内容）。"""
    rows = await search_chunks(
        db,
        query_embedding,
        space_ids=space_ids,
        top_k=top_k,
        min_score=min_score,
    )
    if not rows:
        return []

    doc_ids = {int(row["doc_id"]) for row in rows}
    meta_map = await document_meta(db, doc_ids)
    space_names, category_names = await _name_maps(
        db,
        space_ids={int(row["space_id"]) for row in rows},
        category_ids={meta["category_id"] for meta in meta_map.values() if meta["category_id"]},
    )
    hits: list[KnowledgeHit] = []
    for row in rows:
        meta = meta_map.get(int(row["doc_id"]))
        if meta is None or not meta["is_enabled"]:
            continue
        if meta["status"] != int(DocumentStatus.DONE):
            continue
        space_id = int(row["space_id"])
        category_id = meta["category_id"]
        hits.append(
            KnowledgeHit(
                chunk_id=int(row["id"]),
                space_id=space_id,
                doc_id=int(row["doc_id"]),
                chunk_index=int(row["chunk_index"]),
                page_no=row["page_no"],
                section_title=row["section_title"],
                content=row["content"],
                score=float(row["score"]),
                doc_title=meta["title"],
                file_name=meta["file_name"],
                file_type=meta["file_type"],
                space_name=space_names.get(space_id, ""),
                category_name=category_names.get(category_id, "") if category_id else "",
                meta=dict(row.get("meta") or {}),
            )
        )
    return hits


async def document_meta(db: AsyncSession, doc_ids: Sequence[int]) -> dict[int, dict[str, Any]]:
    """批量取文档元数据（标题/文件名/类型/上架与解析状态），避免逐条查询。"""
    ids = {int(doc_id) for doc_id in doc_ids}
    if not ids:
        return {}
    rows = (
        await db.execute(
            select(
                IntegrationKbDocumentModel.id,
                IntegrationKbDocumentModel.title,
                IntegrationKbDocumentModel.file_name,
                IntegrationKbDocumentModel.file_type,
                IntegrationKbDocumentModel.status,
                IntegrationKbDocumentModel.is_enabled,
                IntegrationKbDocumentModel.category_id,
            ).where(IntegrationKbDocumentModel.id.in_(ids), IntegrationKbDocumentModel.is_deleted.is_(False))
        )
    ).all()
    return {
        int(row[0]): {
            "title": row[1] or "",
            "file_name": row[2] or "",
            "file_type": row[3] or "",
            "status": int(row[4] or 0),
            "is_enabled": bool(row[5]),
            "category_id": int(row[6]) if row[6] else 0,
        }
        for row in rows
    }


async def space_option_list(db: AsyncSession) -> list[dict[str, Any]]:
    """列出可检索空间（问答「全部空间」范围与前端范围选择器共用，停用空间不参与）。"""
    rows = (
        await db.execute(
            select(IntegrationKbSpaceModel.id, IntegrationKbSpaceModel.name, IntegrationKbSpaceModel.code)
            .where(IntegrationKbSpaceModel.status == 0, IntegrationKbSpaceModel.is_deleted.is_(False))
            .order_by(IntegrationKbSpaceModel.sort.asc(), IntegrationKbSpaceModel.id.asc())
        )
    ).all()
    return [{"id": int(row[0]), "name": row[1], "code": row[2]} for row in rows]


async def _name_maps(
    db: AsyncSession,
    *,
    space_ids: set[int],
    category_ids: set[int],
) -> tuple[dict[int, str], dict[int, str]]:
    """批量取空间名与分类名（引用卡片展示用，两次查询避免 N+1）。"""
    space_names: dict[int, str] = {}
    if space_ids:
        rows = (
            await db.execute(
                select(IntegrationKbSpaceModel.id, IntegrationKbSpaceModel.name).where(
                    IntegrationKbSpaceModel.id.in_(space_ids)
                )
            )
        ).all()
        space_names = {int(row[0]): row[1] for row in rows}
    category_names: dict[int, str] = {}
    if category_ids:
        rows = (
            await db.execute(
                select(IntegrationKbCategoryModel.id, IntegrationKbCategoryModel.name).where(
                    IntegrationKbCategoryModel.id.in_(category_ids)
                )
            )
        ).all()
        category_names = {int(row[0]): row[1] for row in rows}
    return space_names, category_names


async def touch_documents(db: AsyncSession, doc_ids: Sequence[int]) -> None:
    """累加文档引用次数（问答命中引用后调用）；失败只告警，不影响回答。"""
    ids = sorted({int(doc_id) for doc_id in doc_ids})
    if not ids:
        return
    try:
        from .document.crud import IntegrationKbDocumentCRUD

        await IntegrationKbDocumentCRUD(db).increment_ref_count(ids)
    except Exception:
        logger.warning("累加文档引用次数失败：doc_ids={}", ids, exc_info=True)
