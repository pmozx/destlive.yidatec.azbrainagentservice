"""`/v1` 知识接口：发布（03.1–03.4）、检索（#07）与停用（#20）。

已实现接口把编排交给 ``service.py``；重复治理的查重/分组/预览（#16–#19）在
``kb/duplicate``，本文件只保留与之同族的“发布单元写入口”——#20 停用与 03.3/03.4
共用 ``/entries/{publication_id}`` 路径与同一个幂等域。
"""

from typing import Annotated

from fastapi import Depends, Request, status

from ...base.common import make_contract_router
from ...base.deps import idempotent_request, record_receipt, replay_response
from ...base.idempotency import Reservation
from .schema import (
    EntryDeactivateReceipt,
    EntryDeactivateRequest,
    EntryDeleteReceipt,
    EntryDeleteRequest,
    EntryQueryRequest,
    EntryQueryResponse,
    EntryWriteReceipt,
    EntryWriteRequest,
    SearchRequest,
    SearchResponse,
)
from .service import (
    create_entry,
    deactivate_entry,
    delete_entry,
    query_entry,
    revise_entry,
    search_knowledge_base,
)

KnowledgeRouter = make_contract_router(prefix="/knowledge", tags=["契约 /v1 知识（03.1–03.4 / #07 / #20）"])


# ── 知识发布：03.1 新增 / 03.2 读取基线 / 03.3 修改 / 03.4 删除 ─────────────


@KnowledgeRouter.post(
    "/entries",
    status_code=status.HTTP_201_CREATED,
    summary="03.1 新增已批准切片集合",
    response_model=EntryWriteReceipt,
)
async def create_knowledge_entry(
    request: Request,
    reservation: Annotated[Reservation, Depends(idempotent_request)],
    payload: EntryWriteRequest,
):
    """T3.2：首发防覆盖（已存在 409）、切片校验、同步向量化。"""
    replayed = replay_response(reservation)
    if replayed is not None:
        return replayed
    receipt = await create_entry(payload)
    await record_receipt(request, reservation, status_code=status.HTTP_201_CREATED, body=receipt)
    return receipt


@KnowledgeRouter.post(
    "/entries/{publication_id}/query",
    summary="03.2 读取知识修订基线",
    response_model=EntryQueryResponse,
)
async def query_knowledge_entry(
    publication_id: str,
    payload: EntryQueryRequest,
):
    """T3.1：只读已批准快照，按历史 `record_id` 精确读取并游标分页。"""
    return await query_entry(publication_id, payload)


@KnowledgeRouter.put(
    "/entries/{publication_id}",
    summary="03.3 修改已批准切片集合",
    response_model=EntryWriteReceipt,
)
async def revise_knowledge_entry(
    publication_id: str,
    request: Request,
    reservation: Annotated[Reservation, Depends(idempotent_request)],
    payload: EntryWriteRequest,
):
    """T3.2：`revision` 单调 + 基线冲突 409 `STALE_REVISION` + 原子版本切换。"""
    replayed = replay_response(reservation)
    if replayed is not None:
        return replayed
    receipt = await revise_entry(publication_id, payload)
    await record_receipt(request, reservation, status_code=status.HTTP_200_OK, body=receipt)
    return receipt


@KnowledgeRouter.delete(
    "/entries/{publication_id}",
    summary="03.4 删除已批准切片集合",
    response_model=EntryDeleteReceipt,
)
async def delete_knowledge_entry(
    publication_id: str,
    request: Request,
    reservation: Annotated[Reservation, Depends(idempotent_request)],
    payload: EntryDeleteRequest,
):
    """T3.2：删除后不可召回，幂等台账留墓碑（`expires_at=NULL`）。"""
    replayed = replay_response(reservation)
    if replayed is not None:
        return replayed
    receipt = await delete_entry(publication_id, payload)
    await record_receipt(request, reservation, status_code=status.HTTP_200_OK, body=receipt, tombstone=True)
    return receipt


# ── 检索：#07 ────────────────────────────────────────────────────────


@KnowledgeRouter.post(
    "/search",
    summary="#07 关键词 / 自然语言检索",
    response_model=SearchResponse,
)
async def search_knowledge(payload: SearchRequest):
    """T4.1 / T4.2：按 ``mode`` 走关键词或语义通道，两条通道共用授权下推与契约投影。"""
    return await search_knowledge_base(payload)


# ── 停用：#20 ──────────────────────────────────────────────────────


@KnowledgeRouter.post(
    "/entries/{publication_id}/deactivate",
    summary="#20 停用已生效知识",
    response_model=EntryDeactivateReceipt,
)
async def deactivate_knowledge_entry(
    publication_id: str,
    request: Request,
    reservation: Annotated[Reservation, Depends(idempotent_request)],
    payload: EntryDeactivateRequest,
):
    """T6.3：停用后不参与检索与引用；保留内容供审计，幂等台账留墓碑（`expires_at=NULL`）。"""
    replayed = replay_response(reservation)
    if replayed is not None:
        return replayed
    receipt = await deactivate_entry(publication_id, payload)
    await record_receipt(request, reservation, status_code=status.HTTP_200_OK, body=receipt, tombstone=True)
    return receipt
