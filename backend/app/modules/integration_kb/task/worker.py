"""知识库文档解析任务：下载原件 → 解析 → 分片 → 向量化 → 写库。

为什么不用 FastAPI BackgroundTasks、也不用 APScheduler：解析是分钟级任务（MinerU 提交 + 轮询），
挂在响应后台任务上会被请求生命周期牵连；调度器在本项目里又被刻意去掉（见 ``integration_kb/__init__``）。
这里用「进程内 asyncio 任务登记表 + 信号量并发闸门 + 独立会话回写」三件事，够用且可观测。

时序口径：作业参数（对象键 / 空间编码 / 文件名）在投递时就带全，任务**不读**请求事务里的行，
避免「任务先于事务提交启动、读不到文档」的竞态；执行结果再用独立会话回写状态。

状态回写：任务跑在请求之外，一律用 ``async_session_factory`` 自开短事务，不复用请求级会话。

撤销口径：删除文档只清「当下已有」的分片与对象；正在跑的解析随后还会写一份，所以回写状态时
若发现文档已不存在，必须把本次写出的分片与产物再清一遍，否则会留下查不到文档的无主分片。

续跑口径：作业带 ``resume_from`` 时从该节点起跑（解析快照复用，不重复调 MinerU、不重复下载原件），
失败只把失败节点标红，其余节点保持「已完成 / 待执行」，人工重试只补跑失败节点及其之后的部分。
"""

import asyncio
import json
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select, update

from app.config.setting import settings
from app.core.database import async_session_factory
from app.core.logger import logger

from ..chunk.vector_store import delete_chunks, vector_store_ready
from ..clients import S3StorageClient
from ..document.model import DocumentStatus, IntegrationKbDocumentModel
from ..pipeline import process_document
from ..pipeline.document_pipeline import STAGE_STARTED
from ..pipeline.parsed_assets import parsed_prefix
from .model import IntegrationKbTaskModel, IntegrationKbTaskStepModel, StepStage, TaskStatus

# 进程内在跑的解析任务：键为文档主键，持有强引用避免任务被 GC 提前回收
_running: dict[int, asyncio.Task[None]] = {}
_semaphore: asyncio.Semaphore | None = None

# 回写状态时等待文档行可见的次数与间隔（请求事务可能尚未提交，见模块 docstring）
_VISIBLE_RETRIES: int = 6
_VISIBLE_INTERVAL: float = 0.2

# 节点完成后的进度：写入 pgvector 之后还有收尾回写，所以最后一档留 95，成功时置 100
_STAGE_PROGRESS: dict[int, int] = {
    int(StepStage.PARSE): 25,
    int(StepStage.CHUNK): 50,
    int(StepStage.EMBED): 75,
    int(StepStage.WRITE): 95,
}

# 节点开始时的进度（等于上一节点的完成值）：阶段续跑从这里接着走，不把进度条打回 0
STAGE_START_PROGRESS: dict[int, int] = {
    int(StepStage.PARSE): 0,
    int(StepStage.CHUNK): 25,
    int(StepStage.EMBED): 50,
    int(StepStage.WRITE): 75,
}


@dataclass(frozen=True)
class ParseJob:
    """一次解析作业的全部入参（投递时固定，与数据库事务提交时序解耦）

    ``resume_from`` 大于解析节点时走阶段续跑：不下载原件、不调用 MinerU，直接吃解析快照；
    ``task_id`` 由重试入口显式带上（复用原任务行），首次上传解析时为空、按台账反查最新任务。
    """

    doc_id: int
    space_id: int
    space_code: str
    object_key: str
    file_name: str
    task_id: int | None = None
    resume_from: int = int(StepStage.PARSE)


def _get_semaphore() -> asyncio.Semaphore:
    """惰性创建并发闸门：Semaphore 绑定事件循环，不能在模块导入期创建。"""
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(max(1, settings.PARSE_THREAD_POOL_SIZE))
        logger.info("==== 文档解析并发闸门就绪（上限 {}）====", settings.PARSE_THREAD_POOL_SIZE)
    return _semaphore


def running_document_ids() -> set[int]:
    """当前在跑的文档ID集合（供接口回显，避免前端误以为卡死）。"""
    return set(_running)


async def _write_document_status(document_id: int, **values: object) -> bool:
    """独立会话回写文档状态；返回文档是否仍存在（删文档即视为撤销解析）。"""
    for attempt in range(_VISIBLE_RETRIES):
        async with async_session_factory() as session, session.begin():
            doc = await session.get(IntegrationKbDocumentModel, document_id)
            if doc is not None:
                for key, value in values.items():
                    setattr(doc, key, value)
                return True
        if attempt < _VISIBLE_RETRIES - 1:
            await asyncio.sleep(_VISIBLE_INTERVAL)
    return False


async def _resolve_latest_task_id(doc_id: int) -> int | None:
    """等任务台账可见并返回其主键。

    投递发生在请求事务提交之前，新插入的任务行可能尚不可见；拿到 task_id 意味着
    事务已提交，此时同事务写入的四个步骤行也一定可见，后续节点回写才不会全部落空。
    """
    for attempt in range(_VISIBLE_RETRIES):
        async with async_session_factory() as session:
            task_id = (
                await session.execute(
                    select(IntegrationKbTaskModel.id)
                    .where(IntegrationKbTaskModel.doc_id == doc_id)
                    .order_by(IntegrationKbTaskModel.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
        if task_id is not None:
            return task_id
        if attempt < _VISIBLE_RETRIES - 1:
            await asyncio.sleep(_VISIBLE_INTERVAL)
    return None


async def _update_task_status(task_id: int | None, **values: object) -> None:
    """回写任务台账（台账只作展示，回写失败不影响解析结论）。"""
    if task_id is None:
        return
    try:
        async with async_session_factory() as session, session.begin():
            await session.execute(update(IntegrationKbTaskModel).where(IntegrationKbTaskModel.id == task_id).values(**values))
    except Exception as exc:
        logger.warning("任务台账回写失败（task_id={}）：{}", task_id, exc)


def _build_stage_hook(task_id: int | None, doc_id: int, state: dict[str, int | None]):
    """把流水线节点事件落成步骤明细，并联动任务/文档进度。

    步骤行在任务登记时已预置为「待执行」，这里只做状态推进：started → 执行中（记开始时间），
    succeeded → 已完成（记耗时、产出条数与摘要）。回写失败只告警，绝不让观测数据拖垮解析。
    """

    async def on_stage(stage: int, phase: str, info: dict[str, Any]) -> None:
        progress = _STAGE_PROGRESS.get(stage, 0)
        # state 只记「确实完成到哪」：节点开始记上一节点完成值，节点成功才记本节点完成值，
        # 这样失败回写不会把没跑完的节点算成已完成
        if phase == STAGE_STARTED:
            state["stage"] = stage
            state["progress"] = STAGE_START_PROGRESS.get(stage, 0)
        else:
            state["progress"] = progress
        if phase == STAGE_STARTED:
            step_values: dict[str, object] = {
                "status": int(TaskStatus.RUNNING),
                "started_time": datetime.now(UTC),
                "error_msg": "",
            }
        else:
            step_values = {
                "status": int(TaskStatus.SUCCESS),
                "finished_time": datetime.now(UTC),
                "elapsed_ms": int(info.get("elapsed_ms") or 0),
                "input_count": int(info.get("input_count") or 0),
                "output_count": int(info.get("output_count") or 0),
                "detail": json.dumps(info.get("detail") or {}, ensure_ascii=False),
            }
        try:
            async with async_session_factory() as session, session.begin():
                if task_id is not None:
                    await session.execute(
                        update(IntegrationKbTaskStepModel)
                        .where(IntegrationKbTaskStepModel.task_id == task_id, IntegrationKbTaskStepModel.stage == stage)
                        .values(**step_values)
                    )
                    await session.execute(
                        update(IntegrationKbTaskModel).where(IntegrationKbTaskModel.id == task_id).values(progress=progress)
                    )
                await session.execute(
                    update(IntegrationKbDocumentModel).where(IntegrationKbDocumentModel.id == doc_id).values(parse_progress=progress)
                )
        except Exception as exc:
            logger.warning("节点状态回写失败（doc_id={}，stage={}，{}）：{}", doc_id, stage, phase, exc)

    return on_stage


async def _mark_stage_failed(task_id: int | None, stage: int, reason: str) -> None:
    """把失败节点置为失败；后续节点保持「待执行」，语义即「未执行到」。"""
    if task_id is None:
        return
    try:
        async with async_session_factory() as session, session.begin():
            await session.execute(
                update(IntegrationKbTaskStepModel)
                .where(
                    IntegrationKbTaskStepModel.task_id == task_id,
                    IntegrationKbTaskStepModel.stage == stage,
                    IntegrationKbTaskStepModel.status.in_([int(TaskStatus.PENDING), int(TaskStatus.RUNNING)]),
                )
                .values(status=int(TaskStatus.FAILED), finished_time=datetime.now(UTC), error_msg=reason[:2000])
            )
    except Exception as exc:
        logger.warning("失败节点回写失败（task_id={}，stage={}）：{}", task_id, stage, exc)


async def _discard_artifacts(space_code: str, doc_id: int) -> None:
    """撤销一次已完成解析的产物（分片 + 解析产物目录）；失败只告警。

    删除文档时清的是「写之前」已有的数据，解析任务随后仍会写一份新分片与新产物，
    这里按 doc_id 再清一次，避免留下无主分片与孤儿对象。
    """
    if vector_store_ready():
        try:
            async with async_session_factory() as session, session.begin():
                removed = await delete_chunks(session, doc_ids=[doc_id])
            logger.info("==== 已撤销无主分片 {} 条（doc_id={}）====", removed, doc_id)
        except Exception as exc:
            logger.error("撤销无主分片失败（doc_id={}）：{}", doc_id, exc)

    client = S3StorageClient()
    if not client.configured:
        return
    try:
        removed = await client.delete_prefix(f"{parsed_prefix(space_code, doc_id)}/")
        if removed:
            logger.info("==== 已撤销无主解析产物 {} 个对象（doc_id={}）====", removed, doc_id)
    except Exception as exc:
        logger.warning("撤销无主解析产物失败（doc_id={}）：{}", doc_id, exc)


async def _run_document(job: ParseJob) -> None:
    """单个文档的解析任务：编排在事件循环，解析/分片在线程池，向量化走异步 HTTP。"""
    async with _get_semaphore():
        started = time.perf_counter()
        # 续跑作业自带任务主键（重试复用原任务行），首次解析才需要等台账提交后可见
        task_id = job.task_id or await _resolve_latest_task_id(job.doc_id)
        start_progress = STAGE_START_PROGRESS.get(job.resume_from, 0)
        await _update_task_status(
            task_id,
            status=int(TaskStatus.RUNNING),
            progress=start_progress,
            error_msg="",
            started_time=datetime.now(UTC),
        )
        # 文档同步进入「解析中」：否则前端轮询看到的始终是待解析，节点进度也刷不出来
        await _write_document_status(
            job.doc_id,
            status=int(DocumentStatus.PARSING),
            parse_progress=start_progress,
            error_msg="",
        )

        # state 记住最后一个 started 的节点与当前进度：异常时据此判定是哪个节点失败
        state: dict[str, int | None] = {"stage": job.resume_from, "progress": start_progress}
        try:
            # 原件以对象存储为准：本地不留副本，解析前按需下载到内存；
            # 续跑自分片及以后时解析快照已含正文，不必再下载原件
            data = None
            if job.resume_from <= int(StepStage.PARSE):
                data = await S3StorageClient().get_object(job.object_key)
            result = await process_document(
                Path(job.file_name),
                data=data,
                space_id=job.space_id,
                doc_id=job.doc_id,
                space_code=job.space_code,
                on_stage=_build_stage_hook(task_id, job.doc_id, state),
                resume_from=job.resume_from,
            )
        except asyncio.CancelledError:
            # 关停时的取消：交给启动期的 reset_interrupted_documents 兜底改回待解析
            logger.warning("==== 文档解析被取消（id={}，{}）====", job.doc_id, job.file_name)
            raise
        except Exception as exc:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            reason = str(getattr(exc, "msg", "") or exc or type(exc).__name__)
            failed_progress = int(state.get("progress") or 0)
            logger.error("==== 文档解析失败（id={}，{}，{}ms）：{} ====", job.doc_id, job.file_name, elapsed_ms, reason)
            await _mark_stage_failed(task_id, int(state.get("stage") or StepStage.PARSE), reason)
            await _write_document_status(
                job.doc_id,
                status=int(DocumentStatus.FAILED),
                parse_progress=failed_progress,
                elapsed_ms=elapsed_ms,
                error_msg=reason[:2000],
                parsed_time=datetime.now(UTC),
            )
            await _update_task_status(
                task_id,
                status=int(TaskStatus.FAILED),
                progress=failed_progress,
                error_msg=reason[:2000],
                finished_time=datetime.now(UTC),
            )
            return

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        written = await _write_document_status(
            job.doc_id,
            status=int(DocumentStatus.DONE),
            parse_progress=100,
            chunk_count=result.chunk_count,
            parsed_chars=result.parsed_chars,
            elapsed_ms=elapsed_ms,
            preview=result.preview,
            error_msg="",
            parsed_time=datetime.now(UTC),
        )
        if not written:
            # 解析期间文档被删除：分片与产物已经写出，补一次撤销而不是留着无主数据
            logger.warning("==== 文档在解析期间被删除，撤销本次分片与产物（id={}，{}）====", job.doc_id, job.file_name)
            await _discard_artifacts(job.space_code, job.doc_id)
            return

        await _update_task_status(
            task_id,
            status=int(TaskStatus.SUCCESS),
            progress=100,
            result=f"解析完成：分片 {result.chunk_count} 条，正文 {result.parsed_chars} 字符",
            error_msg="",
            finished_time=datetime.now(UTC),
        )
        logger.info(
            "==== 文档解析完成（id={}，{}）：分片 {} 条，{} 字符，{}ms ====",
            job.doc_id,
            job.file_name,
            result.chunk_count,
            result.parsed_chars,
            elapsed_ms,
        )


def submit_parse(jobs: Sequence[ParseJob]) -> int:
    """投递解析任务，返回本次新增的任务数（同一文档已有任务在跑则跳过）。"""
    loop = asyncio.get_running_loop()
    submitted = 0
    for job in jobs:
        if job.doc_id in _running:
            continue
        task = loop.create_task(_run_document(job), name=f"sbp-kb-parse-{job.doc_id}")
        _running[job.doc_id] = task
        task.add_done_callback(lambda _task, key=job.doc_id: _running.pop(key, None))
        submitted += 1
    if submitted:
        logger.info("==== 已投递 {} 份文档到解析线程池（并发上限 {}）====", submitted, settings.PARSE_THREAD_POOL_SIZE)
    return submitted


async def cancel_all(timeout: float = 10.0) -> int:
    """关停时取消在跑任务：取消后给状态回写留一点时间，超时则直接放弃。"""
    tasks = [task for task in _running.values() if not task.done()]
    if not tasks:
        return 0
    for task in tasks:
        task.cancel()
    await asyncio.wait(tasks, timeout=timeout)
    logger.info("==== 已取消 {} 个在跑的文档解析任务 ====", len(tasks))
    return len(tasks)


async def reset_interrupted_documents() -> int:
    """启动时把上次进程中断留下的「解析中」改回「待解析」，并关闭未完成的任务台账与步骤。"""
    async with async_session_factory() as session, session.begin():
        result = await session.execute(
            update(IntegrationKbDocumentModel)
            .where(IntegrationKbDocumentModel.status == int(DocumentStatus.PARSING))
            .values(
                status=int(DocumentStatus.PENDING),
                parse_progress=0,
                error_msg="上次解析在服务重启时中断，请重新提交解析",
            )
        )
        count = getattr(result, "rowcount", 0) or 0

        # 先取未完成任务的主键再改状态：步骤明细按 task_id 一起收尾
        unfinished_ids = list(
            (
                await session.execute(
                    select(IntegrationKbTaskModel.id).where(
                        IntegrationKbTaskModel.status.in_([int(TaskStatus.PENDING), int(TaskStatus.RUNNING)])
                    )
                )
            ).scalars()
        )
        if unfinished_ids:
            await session.execute(
                update(IntegrationKbTaskStepModel)
                .where(
                    IntegrationKbTaskStepModel.task_id.in_(unfinished_ids),
                    IntegrationKbTaskStepModel.status.in_([int(TaskStatus.PENDING), int(TaskStatus.RUNNING)]),
                )
                .values(
                    status=int(TaskStatus.FAILED),
                    error_msg="服务重启导致任务中断",
                    finished_time=datetime.now(UTC),
                )
            )
        await session.execute(
            update(IntegrationKbTaskModel)
            .where(IntegrationKbTaskModel.status.in_([int(TaskStatus.PENDING), int(TaskStatus.RUNNING)]))
            .values(
                status=int(TaskStatus.FAILED),
                progress=0,
                error_msg="服务重启导致任务中断",
                finished_time=datetime.now(UTC),
            )
        )
    if count:
        logger.warning("==== 有 {} 份文档的解析在服务重启时中断，已改回待解析 ====", count)
    return count
