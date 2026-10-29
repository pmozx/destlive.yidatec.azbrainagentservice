"""集成-知识库-任务台账模型（表 sbp_ai_integration_kb_task / sbp_ai_integration_kb_task_step）。

任务表只记录「发生过什么」，不承担调度：解析任务由进程内 asyncio 任务执行并回写状态，
没有 APScheduler 扫描 next_retry_time 的行为，因此这里也不提供定时重试字段。

步骤表（``sbp_ai_integration_kb_task_step``）是任务下的节点明细：一个任务固定登记
解析 → 分片 → 向量化 → 写入 pgvector 四行，执行中逐行回写状态、耗时与产出条数；
文档的「当前节点」由最新任务的步骤行推导，不在文档表里冗余存一份，避免两处口径漂移。
"""

from datetime import datetime
from enum import IntEnum

from sqlalchemy import DateTime, Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_model import ModelMixin

INTEGRATION_KB_TASK_TABLE_NAME: str = "sbp_ai_integration_kb_task"
INTEGRATION_KB_TASK_STEP_TABLE_NAME: str = "sbp_ai_integration_kb_task_step"


class TaskStatus(IntEnum):
    """任务状态"""

    PENDING = 0
    RUNNING = 1
    SUCCESS = 2
    FAILED = 3
    CANCELED = 4


class TaskType(IntEnum):
    """任务类型（当前只有文档解析；后续新增重建索引等再补枚举）"""

    PARSE = 1


class StepStage(IntEnum):
    """流水线节点（一个任务固定按 1→4 顺序执行，与前端节点文案同口径）"""

    PARSE = 1
    CHUNK = 2
    EMBED = 3
    WRITE = 4


# 节点文案：供接口回显与日志复用，前端不再各自维护一份
STAGE_LABELS: dict[int, str] = {
    int(StepStage.PARSE): "解析",
    int(StepStage.CHUNK): "分片",
    int(StepStage.EMBED): "向量化",
    int(StepStage.WRITE): "写入 pgvector",
}


class IntegrationKbTaskStepModel(ModelMixin):
    """任务步骤明细（一个任务固定 4 行，执行中逐行回写耗时与产出）"""

    __tablename__: str = INTEGRATION_KB_TASK_STEP_TABLE_NAME
    __table_args__: tuple = (
        Index("ix_sbp_ai_integration_kb_task_step_task_seq", "task_id", "seq"),
        Index("ix_sbp_ai_integration_kb_task_step_doc_stage", "doc_id", "stage"),
        {"comment": "集成-知识库-任务步骤明细表"},
    )

    task_id: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="所属任务ID")
    doc_id: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="关联文档ID")
    space_id: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="所属空间ID")
    stage: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="节点(1:解析 2:分片 3:向量化 4:写入pgvector)")
    seq: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="节点序号(同一任务内 1→4)")
    status: Mapped[int] = mapped_column(
        Integer, default=int(TaskStatus.PENDING), nullable=False, index=True, comment="状态(0:待执行 1:执行中 2:已完成 3:失败 4:取消)"
    )
    input_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="输入条数(本节点处理的对象数)")
    output_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="产出条数(本节点产出的对象数)")
    elapsed_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="节点耗时(毫秒)")
    detail: Mapped[str] = mapped_column(Text, default="", nullable=False, comment="节点摘要(JSON：来源/模式/向量维度等)")
    error_msg: Mapped[str] = mapped_column(Text, default="", nullable=False, comment="失败原因")
    started_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None, nullable=True, comment="开始时间")
    finished_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None, nullable=True, comment="结束时间")


class IntegrationKbTaskModel(ModelMixin):
    """知识任务台账"""

    __tablename__: str = INTEGRATION_KB_TASK_TABLE_NAME
    __table_args__: tuple = (
        Index("ix_sbp_ai_integration_kb_task_doc", "doc_id"),
        Index("ix_sbp_ai_integration_kb_task_status_created", "status", "created_time"),
        {"comment": "集成-知识库-任务台账表"},
    )

    task_type: Mapped[int] = mapped_column(Integer, default=int(TaskType.PARSE), nullable=False, comment="任务类型(1:文档解析)")
    space_id: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="所属空间ID")
    doc_id: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="关联文档ID")
    status: Mapped[int] = mapped_column(Integer, default=int(TaskStatus.PENDING), nullable=False, index=True, comment="状态(0:待执行 1:执行中 2:成功 3:失败 4:取消)")
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="进度(0-100)")
    result: Mapped[str] = mapped_column(Text, default="", nullable=False, comment="执行结果摘要")
    error_msg: Mapped[str] = mapped_column(Text, default="", nullable=False, comment="失败原因")
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="已重试次数(人工触发)")
    started_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None, nullable=True, comment="开始时间")
    finished_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None, nullable=True, comment="结束时间")
