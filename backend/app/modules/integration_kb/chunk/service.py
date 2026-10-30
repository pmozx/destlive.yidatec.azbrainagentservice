"""知识分片服务：按文档浏览分片、人工清理脏分片。

分片与向量统一存在 PGVector（``chunk/vector_store.py``），本层只做「参数翻译 + 出参组装」，
不再维护第二张业务表：写入由解析流水线负责，浏览与删除直接读写向量表。
不提供「手工新增分片」：没有向量的分片永远检索不到，只会让统计口径失真。
"""

from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_schema import PageResultSchema
from app.core.exceptions import CustomException

from .schema import ChunkOutSchema, ChunkQueryParam
from .vector_store import delete_chunks_by_ids, page_chunks

_DEFAULT_ORDER: tuple[tuple[str, str], ...] = (("doc_id", "asc"), ("chunk_index", "asc"))


def _order_pairs(order_by: Any) -> Sequence[tuple[str, str]]:
    """把接口的 ``[{"doc_id": "asc"}]`` 归一成 ``[(field, direction)]``。"""
    if not order_by:
        return _DEFAULT_ORDER
    pairs = [
        (str(field), str(direction))
        for item in order_by
        if isinstance(item, dict)
        for field, direction in item.items()
    ]
    return pairs or _DEFAULT_ORDER


class IntegrationKbChunkService:
    """知识分片服务"""

    def __init__(self, db: AsyncSession) -> None:
        # 向量表由 PGVector 自己持有会话，此处保留 db 仅为接口层依赖注入的一致写法
        self.db = db

    async def page(
        self,
        page_no: int,
        page_size: int,
        search: ChunkQueryParam | None = None,
        order_by: list[dict[str, str]] | None = None,
    ) -> PageResultSchema[ChunkOutSchema]:
        """分页查询分片：默认按 (文档, 序号) 升序，保证「原文顺序」可读。"""
        offset = (page_no - 1) * page_size
        rows, total = await page_chunks(
            space_id=search.space_id if search else None,
            doc_id=search.doc_id if search else None,
            content=search.content if search else None,
            offset=offset,
            limit=page_size,
            order_by=_order_pairs(order_by),
        )
        items = [
            ChunkOutSchema(id=row["id"], content=row["content"], **row["metadata"])
            for row in rows
        ]
        return PageResultSchema(
            page_no=page_no,
            page_size=page_size,
            total=total,
            has_next=offset + len(items) < total,
            items=items,
        )

    async def delete(self, ids: list[str]) -> None:
        """按主键删除分片（物理删除：软删后向量仍在表里仍会被召回）。"""
        if not ids:
            raise CustomException(msg="删除失败，删除对象不能为空")
        await delete_chunks_by_ids(ids)
