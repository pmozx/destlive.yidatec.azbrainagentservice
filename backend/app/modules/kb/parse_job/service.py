"""解析任务服务：受理建行、四态推进（queued→running→succeeded/failed）与按 job_id 查询。

为什么服务自开会话而不是复用请求级会话：``#01`` 在 202 响应后由后台任务继续解析，
请求级事务早已提交/关闭；``#02`` 只读查询也不需要把事务边界挂到 HTTP 层。
"""

from dataclasses import dataclass, replace
from typing import Any

from app.core.database import async_session_factory
from app.core.logger import logger

from ..parse_result.crud import KbParseResultCRUD
from ..parse_result.schema import KbParseResultCreateSchema
from ..parse_result.service import KbParseResultService
from .crud import KbParseJobCRUD
from .model import KbParseJobModel
from .schema import KbParseJobCreateSchema

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"

# 进度口径：受理 0、开始解析 10、终态 100；中间阶段由解析服务按实际推进回写
PROGRESS_QUEUED = 0
PROGRESS_RUNNING = 10
PROGRESS_DONE = 100


@dataclass(frozen=True)
class ParseJobSnapshot:
    """任务快照：状态机字段 + （成功时）产物，供契约层直接投影为响应。"""

    job_id: str
    status: str
    progress: int
    file_meta: dict[str, Any]
    attempt: int = 1
    error_code: str | None = None
    error_message: str | None = None
    chunks: list[dict[str, Any]] | None = None
    pages: list[dict[str, Any]] | None = None
    suggested: dict[str, Any] | None = None
    suggested_doc_type: str | None = None
    warnings: list[str] | None = None
    artifacts: dict[str, Any] | None = None
    content_hash: str | None = None


class KbParseJobService:
    """解析任务服务（建任务 / 推进状态 / 写产物 / 查询）。"""

    def __init__(self) -> None:
        self._results = KbParseResultService()

    async def create_job(
        self,
        *,
        job_id: str,
        resource_type: str,
        file_meta: dict[str, Any],
    ) -> tuple[ParseJobSnapshot, bool]:
        """建任务并立即提交（后台任务在响应后才会读到这一行）。

        返回 ``(快照, 是否本次新建)``：幂等台账过期后同 job_id 重试会命中已存在的任务行，
        此时不能再启动一次解析（否则会重复写产物）；只有新建或仍停在 queued 才重新排队。
        """
        async with async_session_factory() as session, session.begin():
            crud = KbParseJobCRUD(session)
            existing = await crud.get(uuid=job_id)
            if existing is not None:
                return self._snapshot(existing), False
            job = await crud.create(
                data=KbParseJobCreateSchema(uuid=job_id, resource_type=resource_type, file_meta=file_meta)
            )
            return self._snapshot(job), True

    async def mark_running(self, job_id: str, *, attempt: int | None = None) -> bool:
        """进入 running（progress=10），让 #02 轮询能看到执行中而不是一直 queued。"""
        return await self._update(job_id, attempt=attempt, status=STATUS_RUNNING, progress=PROGRESS_RUNNING)

    async def update_progress(self, job_id: str, progress: int, *, attempt: int | None = None) -> bool:
        """回写中间进度（0–100），让长文档解析期间的轮询有可见推进。"""
        return await self._update(
            job_id, attempt=attempt, progress=max(PROGRESS_QUEUED, min(PROGRESS_DONE, progress))
        )

    async def complete(
        self,
        job_id: str,
        *,
        chunks: list[dict[str, Any]],
        pages: list[dict[str, Any]],
        suggested: dict[str, Any] | None,
        suggested_doc_type: str | None,
        warnings: list[str],
        content_hash: str,
        artifacts: dict[str, Any] | None = None,
        attempt: int | None = None,
    ) -> bool:
        """成功终态：产物与状态在同一事务落库，避免「有产物但状态仍 queued」。

        ``attempt`` 不为空时做乐观锁校验：卡死的旧执行迟到的结果直接丢弃，
        否则会把新一轮的失败态覆盖成成功、却带着上一轮的产物。
        """
        async with async_session_factory() as session, session.begin():
            crud = KbParseJobCRUD(session)
            job = await crud.get(uuid=job_id)
            if job is None:
                logger.warning("解析任务不存在，放弃写入产物: job_id={}", job_id)
                return False
            if attempt is not None and job.attempt != attempt:
                logger.warning(
                    "解析任务已被新的执行接管，丢弃本次产物: job_id={} attempt={} current={}",
                    job_id,
                    attempt,
                    job.attempt,
                )
                return False
            await self._results.save(
                KbParseResultCreateSchema(
                    job_id=job_id,
                    chunks=chunks,
                    pages=pages,
                    suggested=suggested,
                    suggested_doc_type=suggested_doc_type,
                    warnings=warnings,
                    artifacts=artifacts,
                    content_hash=content_hash,
                ),
                session=session,
            )
            await crud.update(
                id=job.id,
                data={
                    "status": STATUS_SUCCEEDED,
                    "progress": PROGRESS_DONE,
                    "error_code": None,
                    "error_message": None,
                },
            )
            return True

    async def fail(self, job_id: str, *, error_code: str, error_message: str, attempt: int | None = None) -> bool:
        """失败终态：必须显式落 failed，否则任务会永远停在 queued/running。"""
        return await self._update(
            job_id,
            attempt=attempt,
            status=STATUS_FAILED,
            progress=PROGRESS_DONE,
            error_code=error_code,
            error_message=error_message,
        )

    async def requeue(self, job_id: str) -> tuple[ParseJobSnapshot | None, str | None]:
        """重跑入口：把 failed（或卡死的 running）重新排队，返回 ``(快照, 拒绝原因)``。

        为什么 succeeded 不允许重跑：页面上的切片可能已被人工修订并写入知识平台，
        直接重跑会用新解析结果覆盖它；要重解析请重新提交一个新任务。
        ``attempt + 1`` 同时让仍在跑的旧协程（卡死但未退出）后续写库全部失效。
        """
        async with async_session_factory() as session, session.begin():
            crud = KbParseJobCRUD(session)
            job = await crud.get(uuid=job_id)
            if job is None:
                return None, "解析任务不存在"
            if job.status not in (STATUS_FAILED, STATUS_RUNNING):
                return self._snapshot(job), "任务已成功，重跑会覆盖已审核的产物；如需重解析请重新提交"
            new_attempt = job.attempt + 1
            updated = await crud.update_if_attempt(
                job_uuid=job_id,
                attempt=job.attempt,
                values={
                    "attempt": new_attempt,
                    "status": STATUS_QUEUED,
                    "progress": PROGRESS_QUEUED,
                    "error_code": None,
                    "error_message": None,
                },
            )
            if not updated:
                return self._snapshot(job), "任务刚被另一次重跑接管，请刷新后重试"
        return replace(
            self._snapshot(job),
            attempt=new_attempt,
            status=STATUS_QUEUED,
            progress=PROGRESS_QUEUED,
            error_code=None,
            error_message=None,
        ), None

    async def reset_unfinished(self, *, error_code: str, error_message: str) -> int:
        """启动期中断恢复：把上次进程留下的 queued/running 任务改判 failed，返回行数。

        契约 #01 的解析跑在响应后置任务里，进程重启后任务不复存在；不改判的话
        #02 会永远返回 running，调用方只能无限轮询。失败码由调用方给（本层不依赖契约层）。
        """
        async with async_session_factory() as session, session.begin():
            affected = await KbParseJobCRUD(session).fail_unfinished(
                error_code=error_code,
                error_message=error_message,
                progress=PROGRESS_DONE,
            )
        return affected

    async def get_snapshot(self, job_id: str) -> ParseJobSnapshot | None:
        """按 job_id（即 uuid）查询；成功态一并带出产物。"""
        async with async_session_factory() as session:
            job = await KbParseJobCRUD(session).get(uuid=job_id)
            if job is None:
                return None
            snapshot = self._snapshot(job)
            if job.status != STATUS_SUCCEEDED:
                return snapshot
            result = await KbParseResultCRUD(session).get(job_id=job_id)
            if result is None:
                return snapshot
            return replace(
                snapshot,
                chunks=result.chunks,
                pages=result.pages,
                suggested=result.suggested,
                suggested_doc_type=result.suggested_doc_type,
                warnings=result.warnings,
                artifacts=result.artifacts,
                content_hash=result.content_hash,
            )

    async def _update(self, job_id: str, *, attempt: int | None = None, **values: Any) -> bool:
        """按 uuid 更新状态字段；任务不存在或已被新一轮接管时只告警。

        返回 False 表示本次写入被丢弃（调用方通常无需处理，语义是「这次执行已经作废」）。
        """
        async with async_session_factory() as session, session.begin():
            crud = KbParseJobCRUD(session)
            job = await crud.get(uuid=job_id)
            if job is None:
                logger.warning("解析任务不存在，跳过状态更新: job_id={} values={}", job_id, values)
                return False
            if attempt is not None and job.attempt != attempt:
                logger.warning(
                    "解析任务已被新的执行接管，跳过状态更新: job_id={} attempt={} current={}",
                    job_id,
                    attempt,
                    job.attempt,
                )
                return False
            await crud.update(id=job.id, data=values)
            return True

    @staticmethod
    def _snapshot(job: KbParseJobModel) -> ParseJobSnapshot:
        return ParseJobSnapshot(
            job_id=job.uuid,
            status=job.status,
            progress=job.progress,
            file_meta=dict(job.file_meta or {}),
            attempt=job.attempt,
            error_code=job.error_code,
            error_message=job.error_message,
        )
