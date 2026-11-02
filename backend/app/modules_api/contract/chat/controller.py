"""`/v1` 会话与生成接口（#08–#15）：骨架期统一 501，签名/审计/幂等已生效。"""

from typing import Annotated

from fastapi import Depends, status

from ..common import make_contract_router, request_id_of, skeleton
from ..deps import idempotent_event, idempotent_request, replay_response
from ..idempotency import Reservation
from ..schema import ContractRequest

ChatRouter = make_contract_router(prefix="/chat", tags=["契约 /v1 会话与生成（#08–#15）"])


# ── 会话：#08 / #09 / #10 / #11 / #12 / #13 ──────────────────────────


@ChatRouter.post("/sessions", status_code=status.HTTP_201_CREATED, summary="#08 创建新会话")
async def create_chat_session(
    reservation: Annotated[Reservation, Depends(idempotent_request)],
    payload: ContractRequest | None = None,
):
    """T5.2：`session_id` 由 AI 分配（首轮传空串，POC 疑点 Q4），返回新 id。"""
    replayed = replay_response(reservation)
    if replayed is not None:
        return replayed
    skeleton("#08 创建会话", request_id_of(payload))


@ChatRouter.post("/sessions/query", summary="#09 分页查询我的会话")
async def query_chat_sessions(payload: ContractRequest | None = None):
    """T5.2：游标翻页（`next_cursor` 不透明，不能自行拼接）。"""
    skeleton("#09 会话列表", request_id_of(payload))


@ChatRouter.post("/sessions/{session_id}/query", summary="#10 会话详情（含历史分页）")
async def query_chat_session_detail(session_id: str, payload: ContractRequest | None = None):
    """T5.3：历史分页 + 引用展示 + 知识依赖失效整条隐藏。"""
    skeleton(f"#10 会话 {session_id} 详情", request_id_of(payload))


@ChatRouter.patch("/sessions/{session_id}", summary="#11 修改会话标题")
async def rename_chat_session(
    session_id: str,
    reservation: Annotated[Reservation, Depends(idempotent_request)],
    payload: ContractRequest | None = None,
):
    """T5.2：标题最多 100 字符；他人会话按 404 隐藏存在性。"""
    replayed = replay_response(reservation)
    if replayed is not None:
        return replayed
    skeleton(f"#11 修改会话 {session_id} 标题", request_id_of(payload))


@ChatRouter.delete("/sessions/{session_id}", summary="#12 删除会话")
async def delete_chat_session(
    session_id: str,
    reservation: Annotated[Reservation, Depends(idempotent_event)],
    payload: ContractRequest | None = None,
):
    """T5.2：DELETE 带 JSON 正文；变更事件域幂等，墓碑防「删除后复活」。"""
    replayed = replay_response(reservation)
    if replayed is not None:
        return replayed
    skeleton(f"#12 删除会话 {session_id}", request_id_of(payload))


@ChatRouter.delete("/sessions/{session_id}/messages", summary="#13 清空会话历史")
async def clear_chat_messages(
    session_id: str,
    reservation: Annotated[Reservation, Depends(idempotent_event)],
    payload: ContractRequest | None = None,
):
    """T5.2：保留会话壳；清空后迟到的生成结果直接丢弃。"""
    replayed = replay_response(reservation)
    if replayed is not None:
        return replayed
    skeleton(f"#13 清空会话 {session_id} 历史", request_id_of(payload))


# ── 生成：#14 / #15 ──────────────────────────────────────────────────


@ChatRouter.post("/completions", summary="#14 发送问题并生成回答（非流式）")
async def create_chat_completion(
    reservation: Annotated[Reservation, Depends(idempotent_request)],
    payload: ContractRequest | None = None,
):
    """T5.4：非流式、`contexts` 校验、prompt 组装、引用与越权 403。"""
    replayed = replay_response(reservation)
    if replayed is not None:
        return replayed
    skeleton("#14 生成回答", request_id_of(payload))


@ChatRouter.post("/requests/{target_request_id}/query", summary="#15 查询超时操作的结果")
async def query_chat_request(target_request_id: str, payload: ContractRequest | None = None):
    """T5.5：承接 completion / mutation / delete 三类操作；过期回 409 `RESULT_EXPIRED`。"""
    skeleton(f"#15 查询请求 {target_request_id} 的结果", request_id_of(payload))
