"""提示词模板模型（表 ``sbp_ai_system_prompt_template``）：模板版本化，改版不覆盖历史。"""

from typing import Any

from sqlalchemy import Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_model import ModelMixin


class SystemPromptTemplateModel(ModelMixin):
    """提示词模板（表 sbp_ai_system_prompt_template）。"""

    __tablename__: str = "sbp_ai_system_prompt_template"
    # 自定义 __table_args__ 会整体覆盖 Mixin 的默认索引，这里显式写全
    __table_args__: tuple = (
        UniqueConstraint("name", "version", name="uq_sbp_ai_system_prompt_template_name_version"),
        Index("ix_sbp_ai_system_prompt_template_status_deleted", "status", "is_deleted"),
        Index("ix_sbp_ai_system_prompt_template_created_deleted", "created_time", "is_deleted"),
        Index("ix_sbp_ai_system_prompt_template_module_status", "module", "status"),
        {"comment": "提示词模板（按 name + version 版本化）"},
    )

    name: Mapped[str] = mapped_column(String(100), nullable=False, comment="模板名（同 name 下版本单调递增）")
    module: Mapped[str] = mapped_column(String(16), nullable=False, comment="所属模块：system/kb/voice")
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False, comment="版本号（软删后不复用）")
    template: Mapped[str] = mapped_column(Text, nullable=False, comment="模板正文（变量占位 {{var}}）")
    variables: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, default=None, nullable=True, comment="变量 schema 与默认值"
    )
    remark: Mapped[str | None] = mapped_column(String(500), default=None, nullable=True, comment="变更说明（含改动人）")
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False, comment="draft/active/retired")
