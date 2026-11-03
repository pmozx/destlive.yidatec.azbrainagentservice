"""解析产物模型（表 ``sbp_ai_kb_parse_result``）：#02 返回的完整结果，1:1 绑任务、不可变。

不继承 ``ModelMixin``：解析产物不可变、只按 90 天清理，软删与 uuid 无意义，
契约里的 ``job_id`` 就是 ``parse_job.uuid``。
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, String, text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_model import MappedBase


class KbParseResultModel(MappedBase):
    """解析产物（表 sbp_ai_kb_parse_result）。"""

    __tablename__: str = "sbp_ai_kb_parse_result"
    __table_args__: tuple = (
        UniqueConstraint("job_id", name="uq_sbp_ai_kb_parse_result_job"),
        {"comment": "解析产物（chunks/pages/suggested/warnings，无向量）"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="主键ID")
    job_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="parse_job.uuid")
    chunks: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False, comment="切片数组（无向量）")
    pages: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False, comment="页图预览数组（无能力时为空）")
    suggested: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None, nullable=True, comment="建议元数据")
    suggested_doc_type: Mapped[str | None] = mapped_column(
        String(64), default=None, nullable=True, comment="建议文档分类（仅建议，不改变业务状态）"
    )
    warnings: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False, comment="非致命解析告警")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, comment="产物整体哈希（版本比对）")
    created_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), server_default=text("now()"), nullable=False, comment="创建时间"
    )
