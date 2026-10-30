"""文档流水线：解析 → 分片 → 向量化 → 写入 pgvector 的最短闭环。

口径：
1. 解析与分片都是同步阻塞调用，统一丢进解析线程池，事件循环只留编排与网络 IO；
2. 分片文本与向量按同一顺序对齐后再落库，条数不一致立即失败——错位比失败更难排查；
3. 落库复用 ``chunk.vector_store.upsert_chunks``，按 (space_id, doc_id, chunk_index) 幂等覆盖，
   同一文档重跑不会产生重复分片，也无需先删后插；
4. 解析产物（Markdown / 版面 JSON / 内嵌图片）在解析成功**当下**就落对象存储，键由
   (空间编码, 文档ID) 推导、重跑原地覆盖；落盘失败只告警降级，不影响后续节点；
5. 解析成功后立即写一份续跑快照（正文 + 版面 + 产物键），分片成功后再补写分片清单；
   ``resume_from`` 大于解析节点时直接从快照续跑，某节点失败只需原地重跑该节点，
   不必为一个向量化失败再把整份文档送去 MinerU 解析一遍。
6. 编排走 LangGraph ``StateGraph``（parse → chunk → index）：节点边界即续跑边界，
   任务步骤表回写与节点执行保持同一顺序；接上 ``PostgresSaver`` 后每次执行都留下
   节点级快照，便于定位「卡在哪个节点」。图状态只放可 JSON 序列化的数据
   （续跑快照 dict + 计数），正文/分片仍以对象存储快照为准，避免 checkpoint 膨胀。
"""

import json
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, TypedDict

from fastapi import status
from langgraph.graph import END, StateGraph

from app.core.clients import EmbeddingClient
from app.core.exceptions import CustomException
from app.core.logger import logger
from app.core.rag.checkpointer import get_checkpointer
from app.core.rag.pipeline import build_embedding_rows as core_build_embedding_rows
from app.core.rag.parsers import ParsedDocument, parse_document
from app.utils.text_splitter import TextChunk, split_text

from ..chunk.vector_store import upsert_chunks
from ..clients import S3StorageClient
from ..runtime import run_in_parse_pool
from ..task.model import StepStage
from .parsed_assets import (
    image_content_type,
    normalize_content_list_image_refs,
    normalize_markdown_image_refs,
    parsed_content_list_key,
    parsed_image_key,
    parsed_markdown_key,
    parsed_result_key,
    safe_image_name,
)

# 解析正文预览长度：存文档行供人工核验解析质量，不参与分片与检索
PREVIEW_CHARS: Final[int] = 1000

# 续跑快照版本：载荷结构变更时递增，读到旧版快照按「缺失」处理，让调用方重新解析
BUNDLE_VERSION: Final[int] = 1

# 节点事件回调：worker 据此回写任务步骤表（节点取值复用 task.model.StepStage，单一枚举源）
StageCallback = Callable[[int, str, dict[str, Any]], Awaitable[None]]
STAGE_STARTED: Final[str] = "started"
STAGE_SUCCEEDED: Final[str] = "succeeded"


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


@dataclass(frozen=True)
class ChunkedDocument:
    """解析 + 分片的中间结果（不含向量，便于先人工核验分片质量）。"""

    document: ParsedDocument
    chunks: list[TextChunk]


@dataclass(frozen=True)
class ArtifactSummary:
    """解析产物落盘结果（对象键 + 图片数），供接口回显「能否在线预览」。"""

    markdown_key: str
    content_list_key: str
    image_count: int


@dataclass(frozen=True)
class PipelineResult:
    """一次完整流水线的结果摘要（供接口回显与联调记录）。"""

    doc_id: int
    space_id: int
    chunk_count: int
    vector_dim: int
    parsed_chars: int
    elapsed_ms: int
    preview: str
    artifacts: ArtifactSummary | None = None


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
    document = await run_in_parse_pool(parse_document, path, data=data)
    chunks = await run_in_parse_pool(
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
    space_id: int,
    doc_id: int,
    page_map: Sequence[tuple[str, int]] | None = None,
) -> list[dict[str, Any]]:
    """把分片与向量组装成 upsert 行；条数不一致直接报错，绝不截断对齐。

    ``page_map`` 由解析器提供（MinerU 版面 JSON）：定位器按分片顺序复用同一实例，
    才能做到游标单调前进与「沿用上一块页码」；无版面块（flash 模式）时全部为 None。
    """
    if len(chunks) != len(vectors):
        raise CustomException(
            msg=f"分片与向量条数不一致：分片 {len(chunks)}，向量 {len(vectors)}",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    return core_build_embedding_rows(
        chunks,
        vectors,
        identifiers={"space_id": space_id, "doc_id": doc_id},
        index_field="chunk_index",
        page_map=page_map,
    )


async def persist_artifacts(
    document: ParsedDocument,
    *,
    space_code: str,
    doc_id: int,
    storage: S3StorageClient | None = None,
) -> ArtifactSummary | None:
    """把解析产物（Markdown / 版面 JSON / 内嵌图片）落到对象存储。

    为什么必须留产物：分片只保留「能被检索的碎片」，丢了版面与图片就无法还原原文；
    预览接口直接读 ``full.md``，因此即使 flash 模式（无版面 JSON、无图片）也要留 Markdown。
    失败只告警并返回 None —— 产物是增强项，不能让它把已经成功的分片入库一并拖失败。
    """
    client = storage or S3StorageClient()
    if not client.configured:
        logger.warning("==== 解析产物未落盘：对象存储未启用或配置不全（doc_id={}）====", doc_id)
        return None

    try:
        # 同名图片只留一份：MinerU 结果包里同一图片可能在多个块里重复出现
        images: dict[str, bytes] = {}
        for image in document.images:
            name = safe_image_name(image.name)
            if name and name not in images:
                images[name] = image.data
        for name, data in images.items():
            await client.put_object(
                parsed_image_key(space_code, doc_id, name),
                data,
                content_type=image_content_type(name),
            )

        markdown_key = parsed_markdown_key(space_code, doc_id)
        await client.put_object(
            markdown_key,
            normalize_markdown_image_refs(document.text, images).encode("utf-8"),
            content_type="text/markdown; charset=utf-8",
            metadata={
                "space": space_code,
                "doc-id": doc_id,
                "mode": str(document.meta.get("mode") or ""),
                "pages": str(len(document.page_map)),
            },
        )

        # 版面 JSON 是页码定位的唯一依据：flash 模式没有，留空表示「不支持按页筛选」
        content_list_key = ""
        content_list = normalize_content_list_image_refs(document.content_list, images)
        if content_list:
            content_list_key = parsed_content_list_key(space_code, doc_id)
            await client.put_object(
                content_list_key,
                json.dumps(content_list, ensure_ascii=False, indent=2).encode("utf-8"),
                content_type="application/json; charset=utf-8",
            )
        logger.info(
            "==== 解析产物已落盘（{}）：Markdown + {} 图 + {} 版面块 ====",
            markdown_key,
            len(images),
            len(content_list),
        )
        return ArtifactSummary(markdown_key=markdown_key, content_list_key=content_list_key, image_count=len(images))
    except Exception as exc:
        logger.warning("解析产物落盘失败（doc_id={}，降级为仅保留分片）：{}", doc_id, exc)
        return None


def _stored_image_names(document: ParsedDocument) -> list[str]:
    """归一化后的图片文件名（去重保序，与 :func:`persist_artifacts` 落盘口径一致）。"""
    names: list[str] = []
    for image in document.images:
        name = safe_image_name(image.name)
        if name and name not in names:
            names.append(name)
    return names


def _bundle_payload(
    document: ParsedDocument,
    *,
    artifacts: ArtifactSummary | None,
    image_names: Sequence[str],
    chunks: Sequence[TextChunk] | None = None,
) -> dict[str, Any]:
    """续跑快照载荷：正文 + 版面 + 产物索引 + （可选）分片清单。

    图片字节不回填：它们已作为独立对象落在 ``parsed/images/``，续跑只需要文件名清单
    重建 Markdown 引用。``chunks`` 为 None 表示「分片还没成功」，与「分片结果为空」区分开。
    """
    return {
        "version": BUNDLE_VERSION,
        "source": document.source,
        "suffix": document.suffix,
        "text": document.text,
        "meta": dict(document.meta),
        "page_map": [[text, page] for text, page in document.page_map],
        "content_list": document.content_list,
        "image_names": list(image_names),
        "artifacts": (
            {
                "markdown_key": artifacts.markdown_key,
                "content_list_key": artifacts.content_list_key,
                "image_count": artifacts.image_count,
            }
            if artifacts is not None
            else None
        ),
        "chunks": (
            [{"index": chunk.index, "content": chunk.content, "meta": dict(chunk.meta)} for chunk in chunks]
            if chunks is not None
            else None
        ),
    }


async def _save_bundle(client: S3StorageClient, key: str, payload: dict[str, Any]) -> bool:
    """写续跑快照：失败只告警（最坏情况是下次得从解析节点整段重来，不能拖垮当前流水线）。"""
    if not client.configured:
        logger.warning("==== 续跑快照未写入：对象存储未启用（{}）====", key)
        return False
    try:
        await client.put_object(
            key,
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            content_type="application/json; charset=utf-8",
        )
        return True
    except Exception as exc:
        logger.warning("续跑快照写入失败（{}）：{}", key, exc)
        return False


async def load_parse_bundle(
    space_code: str,
    doc_id: int,
    *,
    storage: S3StorageClient | None = None,
) -> dict[str, Any] | None:
    """读取续跑快照；未启用、不存在、损坏或版本不兼容都返回 None（调用方据此要求重新解析）。"""
    client = storage or S3StorageClient()
    if not client.configured:
        return None
    try:
        raw = await client.get_object(parsed_result_key(space_code, doc_id))
    except CustomException as exc:
        logger.warning("续跑快照不可读（doc_id={}）：{}", doc_id, exc.msg)
        return None
    except Exception as exc:
        logger.warning("续跑快照读取异常（doc_id={}）：{}", doc_id, exc)
        return None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.warning("续跑快照解析失败（doc_id={}）：{}", doc_id, exc)
        return None
    if not isinstance(payload, dict) or payload.get("version") != BUNDLE_VERSION or not isinstance(payload.get("text"), str):
        logger.warning("续跑快照版本或结构不兼容（doc_id={}），按缺失处理", doc_id)
        return None
    return payload


def _document_from_bundle(payload: dict[str, Any]) -> ParsedDocument:
    """从快照重建解析结果（图片字节不重建：续跑只吃正文、版面与分片）。"""
    page_map = [
        (str(item[0]), int(item[1]))
        for item in payload.get("page_map") or []
        if isinstance(item, (list, tuple)) and len(item) == 2
    ]
    content_list = [item for item in payload.get("content_list") or [] if isinstance(item, dict)]
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    return ParsedDocument(
        text=payload["text"],
        source=str(payload.get("source") or ""),
        suffix=str(payload.get("suffix") or ""),
        meta=meta,
        page_map=page_map,
        content_list=content_list,
    )


def _chunks_from_bundle(payload: dict[str, Any]) -> list[TextChunk] | None:
    """从快照取分片清单；未落分片（解析后、分片前失败）或结构异常返回 None。"""
    rows = payload.get("chunks")
    if not isinstance(rows, list) or not rows:
        return None
    chunks: list[TextChunk] = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("content"), str):
            return None
        meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
        chunks.append(TextChunk(index=int(row.get("index") or len(chunks)), content=row["content"], meta=meta))
    return chunks


def _artifacts_from_bundle(payload: dict[str, Any]) -> ArtifactSummary | None:
    """从快照取产物索引；解析时未落盘（对象存储不可用）则为 None。"""
    raw = payload.get("artifacts")
    if not isinstance(raw, dict):
        return None
    markdown_key = str(raw.get("markdown_key") or "")
    if not markdown_key:
        return None
    return ArtifactSummary(
        markdown_key=markdown_key,
        content_list_key=str(raw.get("content_list_key") or ""),
        image_count=int(raw.get("image_count") or 0),
    )


class PipelineState(TypedDict, total=False):
    """图状态：只放可 JSON 序列化的编排产物。

    ``ParsedDocument`` 带图片字节、``TextChunk`` 是 dataclass，都不适合进 checkpoint；
    正文、版面与分片清单统一留在 ``bundle``（对象存储快照）里，节点内部按需重建对象。
    """

    bundle: dict[str, Any]
    parsed_chars: int
    vector_dim: int
    written: int


@dataclass(frozen=True)
class _PipelineDeps:
    """一次流水线执行的不变量（字节、回调、存储客户端不可序列化，故不进图状态）。"""

    path: str | Path
    space_id: int
    doc_id: int
    space_code: str
    data: bytes | None
    chunk_size: int | None
    overlap: int | None
    resume_from: int
    storage: S3StorageClient
    bundle_key: str
    embedding_client: EmbeddingClient
    on_stage: StageCallback | None


async def _notify(deps: _PipelineDeps, stage: StepStage, phase: str, **info: Any) -> None:
    """节点事件回写（调用方不关心步骤表时是空操作）。"""
    if deps.on_stage is not None:
        await deps.on_stage(int(stage), phase, info)


def _build_graph(deps: _PipelineDeps):
    """按本次执行的依赖装配图：parse → chunk → index，节点失败即中断并向上抛。"""

    async def parse_node(state: PipelineState) -> PipelineState:
        if deps.resume_from > int(StepStage.PARSE):
            # 续跑：不重复调用 MinerU，直接吃解析成功时落下的快照（正文 / 版面 / 产物键）
            cached = await load_parse_bundle(deps.space_code, deps.doc_id, storage=deps.storage)
            if cached is None:
                raise CustomException(
                    msg="解析结果缓存缺失，无法从中间节点续跑，请重新解析该文档",
                    status_code=status.HTTP_409_CONFLICT,
                )
            document = _document_from_bundle(cached)
            logger.info(
                "==== 文档续跑自节点 {}（doc_id={}，{}）：复用解析快照 ====",
                StepStage(deps.resume_from).name,
                deps.doc_id,
                document.source,
            )
            return {"bundle": cached, "parsed_chars": len(document.text)}

        await _notify(deps, StepStage.PARSE, STAGE_STARTED)
        started = time.perf_counter()
        document = await run_in_parse_pool(parse_document, deps.path, data=deps.data)
        image_names = _stored_image_names(document)
        await _notify(
            deps,
            StepStage.PARSE,
            STAGE_SUCCEEDED,
            elapsed_ms=_elapsed_ms(started),
            output_count=len(document.text),
            detail={
                "source": document.source,
                "mode": str(document.meta.get("mode") or ""),
                "chars": len(document.text),
                "pages": len(document.page_map),
                "images": len(document.images),
            },
        )
        # 解析成功立刻落产物与续跑快照：后续节点失败时不必再为同一份文档调一次 MinerU
        artifacts = await persist_artifacts(document, space_code=deps.space_code, doc_id=deps.doc_id, storage=deps.storage)
        payload = _bundle_payload(document, artifacts=artifacts, image_names=image_names)
        await _save_bundle(deps.storage, deps.bundle_key, payload)
        return {"bundle": payload, "parsed_chars": len(document.text)}

    async def chunk_node(state: PipelineState) -> PipelineState:
        bundle = state["bundle"]
        document = _document_from_bundle(bundle)
        if deps.resume_from > int(StepStage.CHUNK):
            # 分片已成功过：优先复用快照里的分片清单，避免同一文档因向量化失败而重算分片
            chunks = _chunks_from_bundle(bundle)
            if chunks is not None:
                logger.info("==== 文档续跑复用分片快照（doc_id={}）：{} 块 ====", deps.doc_id, len(chunks))
                return {}

        await _notify(deps, StepStage.CHUNK, STAGE_STARTED)
        started = time.perf_counter()
        chunks = await run_in_parse_pool(
            split_text,
            document.text,
            chunk_size=deps.chunk_size,
            overlap=deps.overlap,
            meta=document.meta,
        )
        if not chunks:
            raise CustomException(
                msg=f"文档解析后无有效内容，未生成分片：{document.source}",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        await _notify(
            deps,
            StepStage.CHUNK,
            STAGE_SUCCEEDED,
            elapsed_ms=_elapsed_ms(started),
            input_count=len(document.text),
            output_count=len(chunks),
            detail={"chars": len(document.text), "chunks": len(chunks)},
        )
        # 分片清单补进快照：向量化 / 写库失败时可直接从这里续跑，不再重算分片
        image_names = [name for name in bundle.get("image_names") or [] if isinstance(name, str)]
        payload = _bundle_payload(
            document,
            artifacts=_artifacts_from_bundle(bundle),
            image_names=image_names,
            chunks=chunks,
        )
        await _save_bundle(deps.storage, deps.bundle_key, payload)
        return {"bundle": payload}

    async def index_node(state: PipelineState) -> PipelineState:
        bundle = state["bundle"]
        document = _document_from_bundle(bundle)
        chunks = _chunks_from_bundle(bundle)
        if chunks is None:
            raise CustomException(
                msg="分片快照缺失，无法续跑向量化节点，请重新解析该文档",
                status_code=status.HTTP_409_CONFLICT,
            )

        await _notify(deps, StepStage.EMBED, STAGE_STARTED)
        started = time.perf_counter()
        vectors = await deps.embedding_client.embed_texts([chunk.content for chunk in chunks])
        vector_dim = len(vectors[0]) if vectors else 0
        await _notify(
            deps,
            StepStage.EMBED,
            STAGE_SUCCEEDED,
            elapsed_ms=_elapsed_ms(started),
            input_count=len(chunks),
            output_count=len(vectors),
            detail={"vector_dim": vector_dim},
        )

        rows = build_embedding_rows(
            chunks,
            vectors,
            space_id=deps.space_id,
            doc_id=deps.doc_id,
            page_map=document.page_map,
        )
        await _notify(deps, StepStage.WRITE, STAGE_STARTED)
        started = time.perf_counter()
        written = await upsert_chunks(rows)
        await _notify(
            deps,
            StepStage.WRITE,
            STAGE_SUCCEEDED,
            elapsed_ms=_elapsed_ms(started),
            input_count=len(rows),
            output_count=written,
            detail={
                "written": written,
                "vector_dim": vector_dim,
                "artifacts": _artifacts_from_bundle(bundle) is not None,
            },
        )
        return {"vector_dim": vector_dim, "written": written}

    builder = StateGraph(PipelineState)
    builder.add_node("parse", parse_node)
    builder.add_node("chunk", chunk_node)
    builder.add_node("index", index_node)
    builder.set_entry_point("parse")
    builder.add_edge("parse", "chunk")
    builder.add_edge("chunk", "index")
    builder.add_edge("index", END)
    return builder.compile(checkpointer=get_checkpointer())


async def process_document(
    path: str | Path,
    *,
    space_id: int,
    doc_id: int,
    space_code: str,
    data: bytes | None = None,
    embedding_client: EmbeddingClient | None = None,
    chunk_size: int | None = None,
    overlap: int | None = None,
    on_stage: StageCallback | None = None,
    resume_from: int = int(StepStage.PARSE),
    thread_id: str | None = None,
) -> PipelineResult:
    """完整流水线：解析 → 分片 → 向量化 → 幂等入库；``resume_from`` 指定从哪个节点起跑。

    调用方手里已有字节（对象存储下载）时传 ``data``，可省一次落盘再读盘；
    ``space_code`` 只用于推导解析产物与续跑快照的对象键（与上传原件同层目录）。

    ``on_stage`` 是节点事件回调（开始 / 成功，携带耗时与产出条数），由调用方决定
    落到任务步骤表；不传则保持纯函数行为（解析器联调、脚本复用）。
    失败不单独回调：异常直接向上抛，调用方按「最后收到的 started 节点」记失败。

    ``resume_from`` 大于解析节点时不调用 MinerU，直接读解析成功时落下的快照；
    大于分片节点且快照里有分片清单时也不再重算分片——失败重试只补跑失败的节点。

    ``thread_id`` 用于 LangGraph 断点：同一 (文档, 任务, 续跑节点) 一个线程，
    每次执行留下节点级快照；不给则不带断点执行（脚本、单测场景）。
    """
    if not space_code or not space_code.strip():
        raise CustomException(msg="space_code 不能为空", status_code=status.HTTP_400_BAD_REQUEST)
    if not int(StepStage.PARSE) <= resume_from <= int(StepStage.WRITE):
        raise CustomException(msg=f"续跑节点非法：{resume_from}", status_code=status.HTTP_400_BAD_REQUEST)

    deps = _PipelineDeps(
        path=path,
        space_id=space_id,
        doc_id=doc_id,
        space_code=space_code,
        data=data,
        chunk_size=chunk_size,
        overlap=overlap,
        resume_from=resume_from,
        storage=S3StorageClient(),
        bundle_key=parsed_result_key(space_code, doc_id),
        embedding_client=embedding_client or EmbeddingClient(),
        on_stage=on_stage,
    )
    graph = _build_graph(deps)

    start = time.perf_counter()
    config = {"configurable": {"thread_id": thread_id}} if thread_id else None
    final: PipelineState = await graph.ainvoke({}, config=config)

    bundle = final["bundle"]
    document = _document_from_bundle(bundle)
    artifacts = _artifacts_from_bundle(bundle)
    vector_dim = int(final.get("vector_dim") or 0)
    written = int(final.get("written") or 0)
    chunk_count = len(_chunks_from_bundle(bundle) or [])
    parsed_chars = int(final.get("parsed_chars") or len(document.text))
    elapsed_ms = int((time.perf_counter() - start) * 1000)
    logger.info(
        "==== 文档流水线完成（{}）：分片 {} 条，向量 {} 维，写入 {} 条，耗时 {}ms）====",
        document.source,
        chunk_count,
        vector_dim,
        written,
        elapsed_ms,
    )
    return PipelineResult(
        doc_id=doc_id,
        space_id=space_id,
        chunk_count=written,
        vector_dim=vector_dim,
        parsed_chars=parsed_chars,
        elapsed_ms=elapsed_ms,
        preview=document.text[:PREVIEW_CHARS],
        artifacts=artifacts,
    )
