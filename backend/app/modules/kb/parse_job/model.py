"""解析任务模型（表 ``sbp_ai_kb_parse_job``）：``uuid`` 即契约 ``job_id``。"""

from typing import Any

from sqlalchemy import CheckConstraint, Index, Integer, String, text, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_model import ModelMixin

# 解析任务四态（契约 #01 202 / #02 200）
PARSE_JOB_STATUSES: tuple[str, ...] = ("queued", "running", "succeeded", "failed")


class KbParseJobModel(ModelMixin):
    """解析任务（表 sbp_ai_kb_parse_job）：`uuid` 即契约 `job_id`。"""

    __tablename__: str = "sbp_ai_kb_parse_job"
    __table_args__: tuple = (
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name="ck_sbp_ai_kb_parse_job_status",
        ),
        CheckConstraint("progress >= 0 AND progress <= 100", name="ck_sbp_ai_kb_parse_job_progress_range"),
        Index("ix_sbp_ai_kb_parse_job_status_created", "status", "created_time"),
        {"comment": "解析任务（#01/#02 状态机载体）"},
    )

    resource_type: Mapped[str] = mapped_column(String(20), nullable=False, comment="document/faq")
    file_meta: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False, comment="原始 metadata（来源/URL/页数）")
    attempt: Mapped[int] = mapped_column(
        Integer, default=1, server_default=text("1"), nullable=False, comment="执行轮次（重跑 +1，写库时做乐观锁）"
    )
    progress: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"), nullable=False, comment="进度 0–100")
    error_code: Mapped[str | None] = mapped_column(String(64), default=None, nullable=True, comment="任务态失败码")
    error_message: Mapped[str | None] = mapped_column(Text, default=None, nullable=True, comment="任务态失败说明")
    status: Mapped[str] = mapped_column(String(20), default="queued", server_default=text("'queued'"), nullable=False, comment="queued/running/succeeded/failed")
