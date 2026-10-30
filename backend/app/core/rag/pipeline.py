"""文档流水线（通用能力）：解析 → 分片 → 向量化 → 向量入库的最短闭环。

口径：
1. 解析与分片都是同步阻塞调用，统一丢进线程池（``asyncio.to_thread``），事件循环只留编排与网络 IO；
2. 分片文本与向量按同一顺序对齐后再落库，条数不一致立即失败——错位比失败更难排查；
3. 向量写入通过 ``writer`` 回调注入，core 不绑定具体业务表；业务侧传入自己的
   ``upsert_chunks`` 封装（内部调用 :func:`app.core.rag.vector_store.upsert_documents`）即可复用整条链路；
4. 标识列（如 ``record_id`` / ``revision`` 或 ``space_id`` / ``doc_id``）由调用方给出，
   core 不假设任何表结构。
"""

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any

from fastapi import status

from app.core.clients import EmbeddingClient
from app.core.exceptions import CustomException
from app.utils.page_locator import PageLocator
from app.utils.text_splitter import TextChunk, split_text

from .parsers import ParsedDocument, parse_document

# 节点事件回调（可选）：携带节点名、阶段（started/succeeded）与该阶段产出信息
StageCallback = Callable[[str, str, dict[str, Any]], Awaitable[None]]
STAGE_PARSE = "parse"
STAGE_CHUNK = "chunk"
STAGE_EMBED = "embed"
STAGE_WRITE = "write"


@dataclass(frozen=True)
class ChunkedDocument:
    """解析 + 分片的中间结果（不含向量，便于先人工核验分片质量）。"""

    document: ParsedDocument
    chunks: list[TextChunk]


@dataclass(frozen=True)
class PipelineResult:
    """一次完整流水线的结果（供接口回显与联调记录）。"""

    chunk_count: int
    vector_dim: int
    parsed_chars: int
    elapsed_ms: int
    document: ParsedDocument
    chunks: list[TextChunk]
    rows: list[dict[str, Any]] = field(default_factory=list)


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


async def _run_sync(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """在默认线程池执行阻塞函数（解析/分片），保持调用签名一致。"""
    return await asyncio.to_thread(partial(func, *args, **kwargs))


async def parse_and_chunk(
    path: str | Path,
    *,
    data: bytes | None = None,
    chunk_size: int | None = None,
    overlap: int | None = None,
) -> ChunkedDocument:
    """只做解析与分片（不进向量库），用于分片质量与解析器联调。

    ``data`` 为调用方已持有的字节（对象存储下载/上传流）：给了就按它解析，
    ``path`` 仅用于取文件名与后缀，不必真的存在于磁盘。
    """
    document = await _run_sync(parse_document, path, data=data)
    chunks = await _run_sync(
        split_text,
        document.text,
        chunk_size=chunk_size,
        overlap=overlap,
        meta=document.meta,
    )
    return ChunkedDocument(document=document, chunks=chunks)


def build_embedding_rows(
    chunks: Sequence[TextChunk],
    vectors: Sequence[Sequence[float]],
    *,
    identifiers: Mapping[str, Any],
    index_field: str = "chunk_index",
    page_map: Sequence[tuple[str, int]] | None = None,
    field_map: Mapping[str, str | None] | None = None,
    extra_fields: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """把分片与向量组装成 upsert 行；条数不一致直接报错，绝不截断对齐。

    ``identifiers`` 是业务标识列（如 ``{"record_id": "r1", "revision": 1}``）；
    ``index_field`` 是分片序号列名（知识平台 ``seq``，集成库 ``chunk_index``）。

    ``field_map`` 用于适配不同业务表字段名，键为通用字段、值为目标列名（``None`` 表示丢弃）：
    - 知识平台示例：``{"page_no": "page_from", "section_title": "heading", "token_count": None, "meta": None}``；
    - 未给出时保持通用名（``page_no`` / ``section_title`` / ``token_count`` / ``meta``）。

    ``extra_fields`` 追加固定列（如 ``publication_id`` / ``status``），逐行原样带上。

    ``page_map`` 由解析器提供：定位器按分片顺序复用同一实例，才能做到游标单调前进
    与「沿用上一块页码」；无版面块时全部为 None。
    """
    if len(chunks) != len(vectors):
        raise CustomException(
            msg=f"分片与向量条数不一致：分片 {len(chunks)}，向量 {len(vectors)}",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    names = {
        "page_no": "page_no",
        "section_title": "section_title",
        "token_count": "token_count",
        "meta": "meta",
    }
    if field_map is not None:
        names.update(field_map)
    locator = PageLocator(page_map)
    rows: list[dict[str, Any]] = []
    for chunk, vector in zip(chunks, vectors, strict=True):
        row = {**identifiers, **(extra_fields or {})}
        row[index_field] = chunk.index
        row["content"] = chunk.content
        optional = {
            "page_no": locator.locate(chunk.content),
            "section_title": chunk.meta.get("section_title") or None,
            "token_count": len(chunk.content),
            "meta": dict(chunk.meta),
        }
        for name, value in optional.items():
            target = names.get(name)
            if target:
                row[target] = value
        row["embedding"] = [float(value) for value in vector]
        rows.append(row)
    return rows


async def embed_chunks(
    chunks: Sequence[TextChunk],
    *,
    embedding_client: EmbeddingClient | None = None,
) -> list[list[float]]:
    """批量向量化分片正文，返回与分片一一对应的向量列表。"""
    if not chunks:
        return []
    client = embedding_client or EmbeddingClient()
    return await client.embed_texts([chunk.content for chunk in chunks])


async def process_document(
    path: str | Path,
    *,
    identifiers: Mapping[str, Any],
    index_field: str = "chunk_index",
    data: bytes | None = None,
    embedding_client: EmbeddingClient | None = None,
    chunk_size: int | None = None,
    overlap: int | None = None,
    field_map: Mapping[str, str | None] | None = None,
    extra_fields: Mapping[str, Any] | None = None,
    writer: Callable[[list[dict[str, Any]]], Awaitable[int]] | None = None,
    on_stage: StageCallback | None = None,
) -> PipelineResult:
    """完整流水线：解析 → 分片 → 向量化 →（可选）写入向量库。

    ``writer`` 为 None 时只跑到「组装好待写入行」为止，方便调用方自行控制事务；
    给了 writer 就把行交给它并以其返回条数作为写入结果。
    """
    async def notify(stage: str, phase: str, **info: Any) -> None:
        if on_stage is not None:
            await on_stage(stage, phase, info)

    started = time.perf_counter()

    await notify(STAGE_PARSE, "started")
    stage_started = time.perf_counter()
    document = await _run_sync(parse_document, path, data=data)
    await notify(
        STAGE_PARSE,
        "succeeded",
        elapsed_ms=_elapsed_ms(stage_started),
        output_count=len(document.text),
        detail={"source": document.source, "chars": len(document.text), "pages": len(document.page_map)},
    )

    await notify(STAGE_CHUNK, "started")
    stage_started = time.perf_counter()
    chunks = await _run_sync(
        split_text,
        document.text,
        chunk_size=chunk_size,
        overlap=overlap,
        meta=document.meta,
    )
    if not chunks:
        raise CustomException(
            msg=f"文档解析后无有效内容，未生成分片：{document.source}",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    await notify(STAGE_CHUNK, "succeeded", elapsed_ms=_elapsed_ms(stage_started), output_count=len(chunks))

    await notify(STAGE_EMBED, "started")
    stage_started = time.perf_counter()
    vectors = await embed_chunks(chunks, embedding_client=embedding_client)
    vector_dim = len(vectors[0]) if vectors else 0
    await notify(STAGE_EMBED, "succeeded", elapsed_ms=_elapsed_ms(stage_started), output_count=len(vectors))

    rows = build_embedding_rows(
        chunks,
        vectors,
        identifiers=identifiers,
        index_field=index_field,
        page_map=document.page_map,
        field_map=field_map,
        extra_fields=extra_fields,
    )
    written = len(rows)
    if writer is not None:
        await notify(STAGE_WRITE, "started")
        stage_started = time.perf_counter()
        written = await writer(rows)
        await notify(STAGE_WRITE, "succeeded", elapsed_ms=_elapsed_ms(stage_started), output_count=written)

    return PipelineResult(
        chunk_count=written,
        vector_dim=vector_dim,
        parsed_chars=len(document.text),
        elapsed_ms=_elapsed_ms(started),
        document=document,
        chunks=chunks,
        rows=rows,
    )
