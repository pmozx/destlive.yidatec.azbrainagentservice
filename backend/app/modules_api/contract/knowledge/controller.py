"""`/v1` 知识接口：写入/读取（#03–#07）与重复治理（#16–#20）。

已实现接口把编排交给 ``service.py``；骨架接口统一走 ``common.skeleton`` 的 501 出口。
"""

from typing import Annotated

from fastapi import Depends, Request, status

from ..common import make_contract_router, request_id_of, skeleton
from ..deps import idempotent_request, record_receipt, replay_response
from ..idempotency import Reservation
from ..schema import ContractRequest
from .schema import EntryDeleteReceipt, EntryDeleteRequest, EntryWriteReceipt, EntryWriteRequest
from .service import create_entry, delete_entry, revise_entry

KnowledgeRouter = make_contract_router(prefix="/knowledge", tags=["契约 /v1 知识（#03–#07 / #16–#20）"])


# ── 知识写入与读取：#03 / #04 / #05 / #06 ──────────────────────────────


@KnowledgeRouter.post(
    "/entries",
    status_code=status.HTTP_201_CREATED,
    summary="#03 首次发布写入正式切片",
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


@KnowledgeRouter.post("/entries/{publication_id}/query", summary="#04 读取当前生效基线")
async def query_knowledge_entry(publication_id: str, payload: ContractRequest | None = None):
    """T3.1：返回 `current_record_id` / `current_revision` 与切片集合。"""
    skeleton(f"#04 读取发布单元 {publication_id} 的基线", request_id_of(payload))


@KnowledgeRouter.put(
    "/entries/{publication_id}",
    summary="#05 修订（版本切换 / 单片删除）",
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
    summary="#06 整单删除",
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


@KnowledgeRouter.post("/search", summary="#07 关键词 / 自然语言检索")
async def search_knowledge(payload: ContractRequest | None = None):
    """T4.1–T4.3：keyword + 向量 + RRF + rerank，检索期实时权限下推（越权 0 命中）。"""
    skeleton("#07 知识检索", request_id_of(payload))


# ── 重复治理：#16 / #17 / #18 / #19 / #20 ────────────────────────────


@KnowledgeRouter.post("/duplicates/check", summary="#16 保存前重复检查")
async def check_duplicates(payload: ContractRequest | None = None):
    """T6.1：按 `scope` 候选清单召回 + 相似度，失败不显示「未发现重复」。"""
    skeleton("#16 保存前查重", request_id_of(payload))


@KnowledgeRouter.post("/duplicates/groups/query", summary="#17 查询重复分组")
async def query_duplicate_groups(payload: ContractRequest | None = None):
    """T6.2：`dup_group` 物化结果分页。"""
    skeleton("#17 查询重复分组", request_id_of(payload))


@KnowledgeRouter.post("/duplicates/groups/{group_id}/query", summary="#18 分组详情与对比依据")
async def query_duplicate_group_detail(group_id: str, payload: ContractRequest | None = None):
    """T6.2：组内条目与对比字段投影。"""
    skeleton(f"#18 查询重复分组 {group_id} 详情", request_id_of(payload))


@KnowledgeRouter.post("/duplicates/merge-preview", summary="#19 合并预览（不落正式库）")
async def merge_preview(payload: ContractRequest | None = None):
    """T6.3：草稿态预览，不写正式索引；返回 `preview_id` / `preview_hash` 与冲突清单。"""
    skeleton("#19 合并预览", request_id_of(payload))


@KnowledgeRouter.post("/entries/{publication_id}/deactivate", summary="#20 停用已生效知识")
async def deactivate_knowledge_entry(
    publication_id: str,
    reservation: Annotated[Reservation, Depends(idempotent_request)],
    payload: ContractRequest | None = None,
):
    """T6.3：停用后不参与检索与引用；保留内容供审计，幂等台账留墓碑。"""
    replayed = replay_response(reservation)
    if replayed is not None:
        return replayed
    skeleton(f"#20 停用发布单元 {publication_id}", request_id_of(payload))
