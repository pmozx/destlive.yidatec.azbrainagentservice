"""`/v1` 解析接口（#01 / #02）：controller 只做参数接收、调 service、包响应。

业务编排在 ``service.py``；状态机与落库在 ``app.modules.kb.parse_job.service``。
"""

from typing import Annotated

from fastapi import BackgroundTasks, Depends, Request, status

from ..common import make_contract_router
from ..deps import idempotent_job, record_receipt, replay_response
from ..errors import NOT_FOUND, ContractApiError
from ..idempotency import Reservation
from .schema import ParseJobReceipt, ParseJobRequest, ParseJobStatus
from .service import (
    accepted_response,
    create_parse_job_record,
    fetch_file,
    get_parse_job_snapshot,
    run_parse_job,
    status_response,
    validate_file_request,
)

ParseRouter = make_contract_router(prefix="/parse", tags=["契约 /v1 解析（#01–#02）"])


@ParseRouter.post(
    "/jobs",
    status_code=status.HTTP_202_ACCEPTED,
    summary="#01 提交文档 / FAQ 解析任务",
    response_model=ParseJobReceipt,
)
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


@ParseRouter.get(
    "/jobs/{job_id}",
    summary="#02 查询解析进度与完整结果",
    response_model=ParseJobStatus,
    response_model_exclude_none=True,
)
async def get_parse_job(job_id: str):
    """T2.2：四态状态机 + progress；`failed` 走 200 + `error_code`（任务失败 ≠ 请求失败）。"""
    snapshot = await get_parse_job_snapshot(job_id)
    if snapshot is None:
        raise ContractApiError(NOT_FOUND, f"解析任务 {job_id} 不存在。")
    return status_response(snapshot)
