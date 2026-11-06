"""解析任务步骤记录（表 ``sbp_ai_kb_parse_step``）：一次执行、一步一行。

为什么单独建表，而不是往 ``sbp_ai_kb_parse_job`` 塞 JSONB：

1. 要按「状态 + 开始时间」扫卡住的步骤（超时改判失败），JSONB 走不了索引；
2. 重跑会追加新的 ``attempt`` 行，历史必须留着——页面要回答「上一轮是哪一步失败的」；
3. ``output`` 是续跑快照（解析文本 + 页码映射、FAQ 切片），读路径与台账不同，
   台账投影一律不带它，避免把几十 KB 文本塞进轮询响应。

``attempt`` 是乐观锁：重跑时任务行 ``attempt += 1``，旧协程写产物/改状态时
条件里必须带自己的 ``attempt``，握手失败就放弃——否则卡死的旧执行迟到的结果
会覆盖新一轮，页面看到的状态与产物不是同一次执行产出的。
"""

from datetime import UTC, datetime
from typing import Any, Sequence

from pydantic import BaseModel
from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, Index, Integer, String, text, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_crud import CRUDBase
from app.core.base_model import MappedBase
from app.core.database import async_session_factory

# 步骤状态：running 只出现在最后一行；非致命步骤失败也记 failed，靠 fatal 区分是否中断任务
STEP_RUNNING = "running"
STEP_SUCCEEDED = "succeeded"
STEP_FAILED = "failed"
STEP_SKIPPED = "skipped"
PARSE_STEP_STATUSES: tuple[str, ...] = (STEP_RUNNING, STEP_SUCCEEDED, STEP_FAILED, STEP_SKIPPED)

# 步骤口径（顺序即执行顺序）：``faq`` 与 ``chunk`` 互斥，按 resource_type 二选一
STEP_EXTRACT = "extract"
STEP_ARTIFACTS = "artifacts"
STEP_FAQ = "faq"
STEP_CHUNK = "chunk"
STEP_PAGES = "pages"
STEP_SUGGEST = "suggest"
STEP_PERSIST = "persist"
PARSE_STEPS: tuple[str, ...] = (
    STEP_EXTRACT,
    STEP_ARTIFACTS,
    STEP_FAQ,
    STEP_CHUNK,
    STEP_PAGES,
    STEP_SUGGEST,
    STEP_PERSIST,
)

# 每步完成后的进度（进度只认这张表，pipeline 不再自带一份数字，避免两处漂移）
STEP_PROGRESS: dict[str, int] = {
    STEP_EXTRACT: 35,
    STEP_ARTIFACTS: 45,
    STEP_FAQ: 55,
    STEP_CHUNK: 55,
    STEP_PAGES: 80,
    STEP_SUGGEST: 95,
    STEP_PERSIST: 100,
}

# 失败即中断任务的步骤；``artifacts`` / ``pages`` 是增强能力，失败只降级成告警
FATAL_STEPS: frozenset[str] = frozenset({STEP_EXTRACT, STEP_FAQ, STEP_CHUNK, STEP_SUGGEST, STEP_PERSIST})


class KbParseStepModel(MappedBase):
    """解析任务步骤记录（表 sbp_ai_kb_parse_step）。"""

    __tablename__: str = "sbp_ai_kb_parse_step"
    __table_args__: tuple = (
        CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'skipped')",
            name="ck_sbp_ai_kb_parse_step_status",
        ),
        Index("ix_sbp_ai_kb_parse_step_job_attempt", "job_id", "attempt", "id"),
        Index("ix_sbp_ai_kb_parse_step_status_started", "status", "started_time"),
        {"comment": "解析任务步骤台账（一次执行一步一行，含续跑快照）"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="主键ID")
    job_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="parse_job.uuid")
    attempt: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"), nullable=False, comment="第几次执行（1 起）")
    step: Mapped[str] = mapped_column(String(32), nullable=False, comment="extract/artifacts/faq/chunk/pages/suggest/persist")
    status: Mapped[str] = mapped_column(String(16), nullable=False, comment="running/succeeded/failed/skipped")
    fatal: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), nullable=False, comment="失败是否中断任务（false=降级继续）"
    )
    started_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False, comment="开始时间"
    )
    finished_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, nullable=True, comment="结束时间（running 为空）"
    )
    duration_ms: Mapped[int | None] = mapped_column(Integer, default=None, nullable=True, comment="耗时（毫秒）")
    error_code: Mapped[str | None] = mapped_column(String(64), default=None, nullable=True, comment="步骤失败码")
    error_message: Mapped[str | None] = mapped_column(Text, default=None, nullable=True, comment="步骤失败原因")
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False, comment="轻量元数据（解析器/页数/条数）")
    output: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, default=None, nullable=True, comment="续跑快照（extract/faq 才有；台账投影不返回）"
    )


class KbParseStepCRUD(CRUDBase[KbParseStepModel, BaseModel, BaseModel]):
    """步骤记录数据层。"""

    def __init__(self, db: AsyncSession) -> None:
        super().__init__(model=KbParseStepModel, db=db)

    async def list_by_job(self, job_id: str) -> list[KbParseStepModel]:
        """按执行顺序（attempt, id）列出全部步骤记录（含历史尝试）。"""
        return list(
            await self.get_list(
                extra_conditions=[KbParseStepModel.job_id == job_id],
                order_by=[{"attempt": "asc"}, {"id": "asc"}],
            )
        )

    async def latest_attempt(self, job_id: str) -> int:
        """该任务已用到的最大执行轮次（无记录返回 0）。"""
        rows = await self.get_list(
            extra_conditions=[KbParseStepModel.job_id == job_id],
            order_by=[{"attempt": "desc"}],
            limit=1,
        )
        return int(rows[0].attempt) if rows else 0


class KbParseStepService:
    """步骤记录写入口：开始 / 成功 / 失败 / 跳过都自开会话（后台任务不带请求事务）。"""

    async def start(self, *, job_id: str, attempt: int, step: str, detail: dict[str, Any] | None = None) -> int:
        """登记一步开始（status=running），返回行 id 供后续回写。"""
        async with async_session_factory() as session, session.begin():
            row = await KbParseStepCRUD(session).create(
                data={
                    "job_id": job_id,
                    "attempt": attempt,
                    "step": step,
                    "status": STEP_RUNNING,
                    "fatal": step in FATAL_STEPS,
                    "started_time": datetime.now(UTC),
                    "detail": detail or {},
                }
            )
            return int(row.id)

    async def finish(
        self,
        step_id: int,
        *,
        status: str = STEP_SUCCEEDED,
        detail: dict[str, Any] | None = None,
        output: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        """登记一步结束：写状态、耗时、失败原因与（可选）续跑快照。

        ``output`` 只在成功时写；失败时保留旧快照没有意义——失败步骤本来就要重算。
        """
        async with async_session_factory() as session, session.begin():
            crud = KbParseStepCRUD(session)
            row = await crud.get(id=step_id)
            if row is None:
                return
            now = datetime.now(UTC)
            values: dict[str, Any] = {
                "status": status,
                "finished_time": now,
                "duration_ms": int((now - row.started_time).total_seconds() * 1000),
            }
            if detail is not None:
                values["detail"] = detail
            if output is not None:
                values["output"] = output
            if error_code is not None:
                values["error_code"] = error_code
            if error_message is not None:
                values["error_message"] = error_message
            await crud.update(id=step_id, data=values)

    async def fail(self, step_id: int, *, error_code: str, error_message: str) -> None:
        """登记一步失败（fatal 由步骤口径决定，调用方无需重复判断）。"""
        await self.finish(step_id, status=STEP_FAILED, error_code=error_code, error_message=error_message)

    async def skip(self, step_id: int, *, reason: str) -> None:
        """登记一步跳过（例如对象存储未启用、无 PDF 页图可渲染）。"""
        await self.finish(step_id, status=STEP_SKIPPED, error_message=reason)

    async def list_steps(self, job_id: str) -> list[KbParseStepModel]:
        """按 job_id 读取步骤记录（含历史尝试，按 attempt/id 升序）。"""
        async with async_session_factory() as session:
            return await KbParseStepCRUD(session).list_by_job(job_id)

    async def stalled_step(self, job_id: str, *, timeout_seconds: float) -> KbParseStepModel | None:
        """返回该任务最早一条「running 且已超时」的步骤；没有则 None（看门狗用）。"""
        async with async_session_factory() as session:
            rows = await KbParseStepCRUD(session).get_list(
                search={"job_id": job_id, "status": STEP_RUNNING},
                order_by=[{"id": "asc"}],
                limit=1,
            )
        if not rows:
            return None
        row = rows[0]
        elapsed = (datetime.now(UTC) - row.started_time).total_seconds()
        return row if elapsed > timeout_seconds else None

    async def completed_before(
        self,
        job_id: str,
        *,
        attempt: int,
        steps: Sequence[str],
    ) -> tuple[bool, dict[str, dict[str, Any]]]:
        """前序步骤在 ``attempt`` 之前的那一轮是否全部走完，并返回可用快照。

        「走完」= succeeded 或 skipped：非 PDF 任务的 pages 永远是 skipped，
        把它当成未完成会让这类任务永远只能全量重跑。
        返回 ``(可否续跑, {步骤: 快照})``；skipped 步骤没有快照。
        """
        if not steps:
            return True, {}
        async with async_session_factory() as session:
            rows = await KbParseStepCRUD(session).get_list(
                extra_conditions=[
                    KbParseStepModel.job_id == job_id,
                    KbParseStepModel.step.in_(tuple(steps)),
                    KbParseStepModel.attempt < attempt,
                ],
                order_by=[{"attempt": "asc"}, {"id": "asc"}],
            )
        if not rows:
            return False, {}
        # 只看最近一轮：更早的轮次可能被人工放弃过，拿它续跑会把两次执行的中间态混在一起
        prev = rows[-1].attempt
        latest = {row.step: row for row in rows if row.attempt == prev}
        for step in steps:
            row = latest.get(step)
            if row is None or row.status in (STEP_FAILED, STEP_RUNNING):
                return False, {}
        outputs = {
            step: dict(row.output or {})
            for step, row in latest.items()
            if row.status == STEP_SUCCEEDED and row.output
        }
        return True, outputs

    async def first_unfinished(self, job_id: str, *, attempt: int) -> str | None:
        """上一轮（``attempt``）里第一个 failed / running 的步骤；全成功返回 None。

        running 也算：进程被杀/连接半死时进程不会回来写 failed，只留一个永不结束的
        running 行，那正是需要从它续跑的现场。
        """
        async with async_session_factory() as session:
            rows = await KbParseStepCRUD(session).get_list(
                extra_conditions=[
                    KbParseStepModel.job_id == job_id,
                    KbParseStepModel.attempt == attempt,
                    KbParseStepModel.status.in_((STEP_FAILED, STEP_RUNNING)),
                ],
                order_by=[{"id": "asc"}],
                limit=1,
            )
        return rows[0].step if rows else None