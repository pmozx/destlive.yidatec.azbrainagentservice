"""问答取证数据层：按命中回查**切片原文**（引用必须逐字，不能只信检索结果）。

为什么不是直接复用检索层的结果：``SearchHit`` 只带 ``snippet``，契约要求 ``quote`` 能在
对应切片的授权正文里逐字找到（还要能按切片状态剔除已删除切片），回查一次库才拿得到
「此刻仍在库里」的原文；直接拿检索结果拼接会在 GSI 侧校验失败。

为什么按 ``(publication_id, record_id, chunk_id)`` 三元组定位而不是主键：``chunk_id`` 在
切片未落 ID 时由检索层按 ``record_id:seq`` 兜底生成（与 03.2 口径一致），契约里看到的
就是这个值；回查必须认同一个兜底键，否则「命中的 chunk_id」与「回查的 chunk_id」会对不上。
"""

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..chunk.model import KbChunkModel


@dataclass(frozen=True)
class ChunkKey:
    """一条证据的定位键（与契约里的 ``publication_id / record_id / chunk_id`` 一一对应）。"""

    publication_id: str
    record_id: str
    chunk_id: str


class KbChatEvidenceCRUD:
    """只读取证：按命中键批量回查切片正文，任何写操作都不在这里。"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def chunk_contents(self, keys: Sequence[ChunkKey]) -> dict[ChunkKey, str]:
        """批量回查正文；已删除切片不会出现在结果里（调用方据此剔除该条证据）。

        一次查询按 ``publication_id + record_id`` 收窄，再在内存里按兜底键对齐：
        逐条查会变成 N 次往返，而 ``chunk_id`` 本身不保证全局唯一，不能单靠它过滤。
        """
        if not keys:
            return {}
        publications = {key.publication_id for key in keys}
        records = {key.record_id for key in keys}
        rows = await self.db.execute(
            select(
                KbChunkModel.publication_id,
                KbChunkModel.record_id,
                KbChunkModel.chunk_id,
                KbChunkModel.seq,
                KbChunkModel.content,
            ).where(
                KbChunkModel.publication_id.in_(publications),
                KbChunkModel.record_id.in_(records),
            )
        )
        contents: dict[ChunkKey, str] = {}
        for row in rows.all():
            chunk_id = row.chunk_id or f"{row.record_id}:{row.seq}"
            contents[ChunkKey(publication_id=row.publication_id, record_id=row.record_id, chunk_id=chunk_id)] = row.content
        return {key: contents[key] for key in keys if key in contents}
