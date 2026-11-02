"""正式 `/v1` 契约路由骨架（T1.5）：20 条接口先接通签名 / 审计 / 幂等，业务随 Tx 落地。

为什么骨架也值得先挂：
1. **契约冒烟要能跑通**：GSI Mock 只要带上四头签名就能拿到确定的响应（不再是 404），
   联调前就能验证「签名算法、错误体、审计留痕」三件事在本侧是通的；
2. **审计要覆盖全量**：20 条接口全部走 `ContractApiRoute`（继承 `ApiLogRoute`），
   被拒请求也落 `sbp_ai_system_api_log`，不用等到业务写完才补审计；
3. **幂等键要提前占位**：写操作已按三类域接入台账（`job` / `request` / `event`），
   后续 Tx 只需要往 `request.state.idempotency_result` 里塞回执，登记与回放逻辑不用改。

骨架期响应口径（仅新增实现替换，不改契约）：
- 业务未实现统一回 **501** + `INTERNAL_ERROR`（`retryable=false`，重试无意义）；
  契约没有「未实现」专用码，选 `INTERNAL_ERROR` 是因为它本身就是 5xx 兜底码；
- 到达的 `authorization` / `scope` 块**当场校验时效**（跨度 ≤120s、未过期），
  字段必填性随各 Tx 落地 —— 这样 T1.1 的时效校验现在就能被冒烟覆盖。

路径 / 方法 / 状态码与手册 §五「接口总览」逐条对齐；注释里的 `#NN` 就是手册编号。
"""

from typing import Annotated, Any, NoReturn

from fastapi import APIRouter, BackgroundTasks, Depends, Request, status

from app.core.request_body import get_json_body

from .deps import (
    idempotent_event,
    idempotent_job,
    idempotent_request,
    record_receipt,
    replay_response,
    verified_signature,
)
from .entries import EntryDeleteRequest, EntryWriteRequest, create_entry, delete_entry, revise_entry
from .errors import INTERNAL_ERROR, NOT_FOUND, ContractApiError, ContractApiRoute, ContractErrorResponse
from .idempotency import Reservation
from .parse import (
    ParseJobRequest,
    accepted_response,
    create_parse_job_record,
    fetch_file,
    get_parse_job_snapshot,
    run_parse_job,
    status_response,
    validate_file_request,
)
from .signature import verify_authorization

# 每个 /v1 操作都会出现的响应。写进 responses 才能在 Swagger 上看到错误体：
# 本组路由的错误体是裸 `{"error": {...}}`，不展示就只剩状态码，联调时无从对字段。
COMMON_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ContractErrorResponse, "description": "参数校验失败（`INVALID_REQUEST` / `SCOPE_REQUIRED`）"},
    401: {
        "model": ContractErrorResponse,
        "description": "签名缺失 / 不符 / 时间戳越窗 / nonce 重放 / 授权块过期（`UNAUTHORIZED`），带 `WWW-Authenticate`",
    },
    403: {"model": ContractErrorResponse, "description": "无访问权限或治理范围失效（`CONTEXT_OUT_OF_SCOPE`）"},
    404: {"model": ContractErrorResponse, "description": "资源不存在（`NOT_FOUND`）"},
    409: {
        "model": ContractErrorResponse,
        "description": "幂等正文不一致 / 旧修订 / 来源变化 / 结果过期（`IDEMPOTENCY_CONFLICT` / `STALE_REVISION`）",
    },
    413: {"model": ContractErrorResponse, "description": "正文或资源清单超过容量（`PAYLOAD_TOO_LARGE`）"},
    # FastAPI 有请求体/路径参数时默认会记一笔 422。本组路由已把参数校验归一成 400，
    # 这里占位改写描述，避免联调方按 422 写错误分支。
    422: {
        "model": ContractErrorResponse,
        "description": "本组**不会返回 422**：参数校验失败已归一为 400 `INVALID_REQUEST`（本条仅用于抑制默认说明）",
    },
    429: {"model": ContractErrorResponse, "description": "触发限流（`RATE_LIMITED`，`retryable=true`，带 `Retry-After`）"},
    501: {
        "model": ContractErrorResponse,
        "description": "**骨架期占位**：接口已接入签名/审计/幂等，业务实现待对应 Tx 交付（`INTERNAL_ERROR`）",
    },
    503: {"model": ContractErrorResponse, "description": "依赖不可用，如权限数据 / 防重放台账（`AUTHORIZATION_UNAVAILABLE`）"},
}

SKELETON_STATUS = status.HTTP_501_NOT_IMPLEMENTED


def _skeleton(operation: str, request_id: str | None = None) -> NoReturn:
    """骨架期统一出口：明确的 501 + 契约错误体，替代「404 当未实现」的含糊表现。"""
    raise ContractApiError(
        INTERNAL_ERROR,
        f"{operation} 路由骨架已接入（签名 / 审计 / 幂等已生效），业务实现待后续任务交付。",
        status_code=SKELETON_STATUS,
        retryable=False,
        request_id=request_id,
    )


def _request_id_of(body: dict[str, Any] | None) -> str | None:
    """错误回带用：正文有 request_id 就原样带回（手册「能识别请求时原样返回」）。"""
    value = (body or {}).get("request_id")
    return str(value) if value not in (None, "") else None


async def enforced_authorization(request: Request) -> None:
    """校验正文里到达的 `authorization` / `scope` 块：跨度 ≤120s、且未过期（T1.1）。

    骨架期用「到达即校验」而不是「必填校验」：字段必填性随各 Tx 按手册逐字段落地，
    这里先把最容易被误用的时效口径钉住（POC 疑点 Q1/Q2）。
    """
    payload = await get_json_body(request)
    if not isinstance(payload, dict):
        return
    verify_authorization(payload.get("authorization") or payload.get("scope"))


contract_router = APIRouter(
    prefix="/v1",
    tags=["契约 /v1（P1 接入层骨架）"],
    route_class=ContractApiRoute,
    dependencies=[Depends(verified_signature), Depends(enforced_authorization)],
    responses=COMMON_RESPONSES,
)


# ── 解析：#01 / #02 ──────────────────────────────────────────────────────


@contract_router.post("/parse/jobs", status_code=status.HTTP_202_ACCEPTED, summary="#01 提交文档 / FAQ 解析任务")
async def create_parse_job(
    request: Request,
    background: BackgroundTasks,
    reservation: Annotated[Reservation, Depends(idempotent_job)],
    payload: ParseJobRequest,
):
    """T2.1：202 受理 + `job_id` 幂等 + `file_url` 拉取与 10MB/类型校验。"""
    replayed = replay_response(reservation)
    if replayed is not None:
        return replayed

    validate_file_request(payload)
    # 先同步拉完文件：超限 / 类型不支持 / 地址不可读都要在 202 之前变成请求级错误
    content = await fetch_file(payload.file_url)
    snapshot, created = await create_parse_job_record(payload)
    if created or snapshot.status == "queued":
        # 任务行已提交后再挂后置任务：否则后台读不到未提交的行（POC 期坑）
        background.add_task(
            run_parse_job,
            job_id=payload.job_id,
            file_name=payload.file_name,
            data=content,
            chunk_enabled=payload.options.chunk,
        )
    receipt = accepted_response(payload, snapshot)
    await record_receipt(request, reservation, status_code=status.HTTP_202_ACCEPTED, body=receipt)
    return receipt


@contract_router.get("/parse/jobs/{job_id}", summary="#02 查询解析进度与完整结果")
async def get_parse_job(job_id: str):
    """T2.2：四态状态机 + progress；`failed` 走 200 + `error_code`（任务失败 ≠ 请求失败）。"""
    snapshot = await get_parse_job_snapshot(job_id)
    if snapshot is None:
        raise ContractApiError(NOT_FOUND, f"解析任务 {job_id} 不存在。")
    return status_response(snapshot)


# ── 知识写入与读取：#03 / #04 / #05 / #06 ──────────────────────────────


@contract_router.post("/knowledge/entries", status_code=status.HTTP_201_CREATED, summary="#03 首次发布写入正式切片")
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


@contract_router.post("/knowledge/entries/{publication_id}/query", summary="#04 读取当前生效基线")
async def query_knowledge_entry(publication_id: str, body: dict[str, Any] | None = None):
    """T3.1：返回 `current_record_id` / `current_revision` 与切片集合。"""
    _skeleton(f"#04 读取发布单元 {publication_id} 的基线", _request_id_of(body))


@contract_router.put("/knowledge/entries/{publication_id}", summary="#05 修订（版本切换 / 单片删除）")
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


@contract_router.delete("/knowledge/entries/{publication_id}", summary="#06 整单删除")
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


@contract_router.post("/knowledge/search", summary="#07 关键词 / 自然语言检索")
async def search_knowledge(body: dict[str, Any] | None = None):
    """T4.1–T4.3：keyword + 向量 + RRF + rerank，检索期实时权限下推（越权 0 命中）。"""
    _skeleton("#07 知识检索", _request_id_of(body))


# ── 会话：#08 / #09 / #10 / #11 / #12 / #13 ──────────────────────────


@contract_router.post("/chat/sessions", status_code=status.HTTP_201_CREATED, summary="#08 创建新会话")
async def create_chat_session(
    reservation: Annotated[Reservation, Depends(idempotent_request)],
    body: dict[str, Any] | None = None,
):
    """T5.2：`session_id` 由 AI 分配（首轮传空串，POC 疑点 Q4），返回新 id。"""
    replayed = replay_response(reservation)
    if replayed is not None:
        return replayed
    _skeleton("#08 创建会话", _request_id_of(body))


@contract_router.post("/chat/sessions/query", summary="#09 分页查询我的会话")
async def query_chat_sessions(body: dict[str, Any] | None = None):
    """T5.2：游标翻页（`next_cursor` 不透明，不能自行拼接）。"""
    _skeleton("#09 会话列表", _request_id_of(body))


@contract_router.post("/chat/sessions/{session_id}/query", summary="#10 会话详情（含历史分页）")
async def query_chat_session_detail(session_id: str, body: dict[str, Any] | None = None):
    """T5.3：历史分页 + 引用展示 + 知识依赖失效整条隐藏。"""
    _skeleton(f"#10 会话 {session_id} 详情", _request_id_of(body))


@contract_router.patch("/chat/sessions/{session_id}", summary="#11 修改会话标题")
async def rename_chat_session(
    session_id: str,
    reservation: Annotated[Reservation, Depends(idempotent_request)],
    body: dict[str, Any] | None = None,
):
    """T5.2：标题最多 100 字符；他人会话按 404 隐藏存在性。"""
    replayed = replay_response(reservation)
    if replayed is not None:
        return replayed
    _skeleton(f"#11 修改会话 {session_id} 标题", _request_id_of(body))


@contract_router.delete("/chat/sessions/{session_id}", summary="#12 删除会话")
async def delete_chat_session(
    session_id: str,
    reservation: Annotated[Reservation, Depends(idempotent_event)],
    body: dict[str, Any] | None = None,
):
    """T5.2：DELETE 带 JSON 正文；变更事件域幂等，墓碑防「删除后复活」。"""
    replayed = replay_response(reservation)
    if replayed is not None:
        return replayed
    _skeleton(f"#12 删除会话 {session_id}", _request_id_of(body))


@contract_router.delete("/chat/sessions/{session_id}/messages", summary="#13 清空会话历史")
async def clear_chat_messages(
    session_id: str,
    reservation: Annotated[Reservation, Depends(idempotent_event)],
    body: dict[str, Any] | None = None,
):
    """T5.2：保留会话壳；清空后迟到的生成结果直接丢弃。"""
    replayed = replay_response(reservation)
    if replayed is not None:
        return replayed
    _skeleton(f"#13 清空会话 {session_id} 历史", _request_id_of(body))


# ── 生成：#14 / #15 ──────────────────────────────────────────────────


@contract_router.post("/chat/completions", summary="#14 发送问题并生成回答（非流式）")
async def create_chat_completion(
    reservation: Annotated[Reservation, Depends(idempotent_request)],
    body: dict[str, Any] | None = None,
):
    """T5.4：非流式、`contexts` 校验、prompt 组装、引用与越权 403。"""
    replayed = replay_response(reservation)
    if replayed is not None:
        return replayed
    _skeleton("#14 生成回答", _request_id_of(body))


@contract_router.post("/chat/requests/{target_request_id}/query", summary="#15 查询超时操作的结果")
async def query_chat_request(target_request_id: str, body: dict[str, Any] | None = None):
    """T5.5：承接 completion / mutation / delete 三类操作；过期回 409 `RESULT_EXPIRED`。"""
    _skeleton(f"#15 查询请求 {target_request_id} 的结果", _request_id_of(body))


# ── 重复治理：#16 / #17 / #18 / #19 / #20 ────────────────────────────


@contract_router.post("/knowledge/duplicates/check", summary="#16 保存前重复检查")
async def check_duplicates(body: dict[str, Any] | None = None):
    """T6.1：按 `scope` 候选清单召回 + 相似度，失败不显示「未发现重复」。"""
    _skeleton("#16 保存前查重", _request_id_of(body))


@contract_router.post("/knowledge/duplicates/groups/query", summary="#17 查询重复分组")
async def query_duplicate_groups(body: dict[str, Any] | None = None):
    """T6.2：`dup_group` 物化结果分页。"""
    _skeleton("#17 查询重复分组", _request_id_of(body))


@contract_router.post("/knowledge/duplicates/groups/{group_id}/query", summary="#18 分组详情与对比依据")
async def query_duplicate_group_detail(group_id: str, body: dict[str, Any] | None = None):
    """T6.2：组内条目与对比字段投影。"""
    _skeleton(f"#18 查询重复分组 {group_id} 详情", _request_id_of(body))


@contract_router.post("/knowledge/duplicates/merge-preview", summary="#19 合并预览（不落正式库）")
async def merge_preview(body: dict[str, Any] | None = None):
    """T6.3：草稿态预览，不写正式索引；返回 `preview_id` / `preview_hash` 与冲突清单。"""
    _skeleton("#19 合并预览", _request_id_of(body))


@contract_router.post("/knowledge/entries/{publication_id}/deactivate", summary="#20 停用已生效知识")
async def deactivate_knowledge_entry(
    publication_id: str,
    reservation: Annotated[Reservation, Depends(idempotent_request)],
    body: dict[str, Any] | None = None,
):
    """T6.3：停用后不参与检索与引用；保留内容供审计，幂等台账留墓碑。"""
    replayed = replay_response(reservation)
    if replayed is not None:
        return replayed
    _skeleton(f"#20 停用发布单元 {publication_id}", _request_id_of(body))
