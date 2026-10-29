"""system 模块表模型（P1 / T1.0）：防重放、幂等、请求台账、提示词模板、授权缓存。

表名统一 `sbp_ai_system_*`；审计表 `sbp_ai_system_api_log` 在 `api_log/model.py`（基座既有表，本期扩列）。

两类模型刻意用不同基类：
1. 业务表（`prompt_template`）继承 `ModelMixin`：需要 `uuid` / 软删 / `status`，未来有管理端界面；
2. 技术台账（`nonce` / `idempotency` / `request_log` / `auth_cache`）直接继承 `MappedBase`：
   它们按过期时间物理清理、按唯一约束判重，软删会污染唯一约束（删过的 nonce 仍占位反而正确，
   但删过的幂等键必须能重新使用），因此不套软删。

与设计文档的两处口径差异（已回写 02-4 交付说明）：
- `nonce.nonce` 取 varchar(128)：契约允许 16–128 位，若按文档的 64 位建模，合法 nonce 会在入库时炸 500；
- `idempotency.expires_at` 允许为 NULL：墓碑（tombstone）记录不清理，NULL 即"永不过期"。
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_model import MappedBase, ModelMixin

# 幂等域（契约 C3）：解析任务 / 生成与写请求 / 变更事件
IDEMPOTENCY_SCOPES: tuple[str, ...] = ("job", "request", "event")


class SystemNonceModel(MappedBase):
    """防重放台账（表 sbp_ai_system_nonce）：唯一约束是"首用"的唯一事实来源。

    以 INSERT 争用 `uq_..._client_nonce` 判定首用，冲突即重放；不用"先查后插"，
    否则并发重放会同时通过检查。跨实例共享、重启有效，取代 POC 的进程内字典。
    """

    __tablename__: str = "sbp_ai_system_nonce"
    __table_args__: tuple = (
        UniqueConstraint("client_id", "nonce", name="uq_sbp_ai_system_nonce_client_nonce"),
        Index("ix_sbp_ai_system_nonce_expires", "expires_at"),
        {"comment": "防重放 nonce 登记表（首用时间 + 过期清理）"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="主键ID")
    client_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="调用方标识")
    nonce: Mapped[str] = mapped_column(String(128), nullable=False, comment="nonce 原文（契约 16–128 位）")
    request_id: Mapped[str | None] = mapped_column(String(64), default=None, nullable=True, comment="关联请求（重放取证）")
    created_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False, comment="首用时间"
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, comment="过期时间（首用 + 时间窗，默认 10 分钟）"
    )


class SystemIdempotencyModel(MappedBase):
    """幂等台账（表 sbp_ai_system_idempotency）：三类幂等域共用。

    `status` 取值 `processing / succeeded / failed / tombstone`；
    墓碑（删除类操作）`expires_at` 为 NULL，永不清理，用于拒绝"删除后复活"。
    """

    __tablename__: str = "sbp_ai_system_idempotency"
    __table_args__: tuple = (
        UniqueConstraint("scope", "biz_id", name="uq_sbp_ai_system_idempotency_scope_biz"),
        Index("ix_sbp_ai_system_idempotency_expires", "expires_at"),
        {"comment": "幂等台账（job / request / event 三类域）"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="主键ID")
    scope: Mapped[str] = mapped_column(String(32), nullable=False, comment="幂等域：job/request/event")
    biz_id: Mapped[str] = mapped_column(String(128), nullable=False, comment="幂等键（job_id/request_id/event_id）")
    body_hash: Mapped[str] = mapped_column(String(64), nullable=False, comment="请求体 SHA-256（异正文判 409）")
    status: Mapped[str] = mapped_column(String(20), default="processing", nullable=False, comment="台账状态")
    result_ref: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, default=None, nullable=True, comment="首次结果摘要（回放返回，不存正文全文）"
    )
    created_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False, comment="创建时间"
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, nullable=True, comment="保留到期（NULL=墓碑，永不清理）"
    )


class SystemRequestLogModel(MappedBase):
    """请求台账（表 sbp_ai_system_request_log）：#15 超时结果查询的承接方。

    与幂等台账分工：幂等管"重试返回首次结果"，本表管"超时后查询最终结果"；
    过期后查询返回 `RESULT_EXPIRED` 而不是 404。
    """

    __tablename__: str = "sbp_ai_system_request_log"
    __table_args__: tuple = (
        UniqueConstraint("request_id", name="uq_sbp_ai_system_request_log_request_id"),
        Index("ix_sbp_ai_system_request_log_status_created", "status", "created_time"),
        {"comment": "生成/写操作结果台账（超时结果查询）"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="主键ID")
    request_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="契约请求 ID")
    operation: Mapped[str] = mapped_column(String(20), nullable=False, comment="completion/mutation/delete")
    status: Mapped[str] = mapped_column(String(20), default="running", nullable=False, comment="running/succeeded/failed/expired")
    result_visible: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, comment="结果是否仍可返回")
    result_ref: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None, nullable=True, comment="结果引用")
    error_code: Mapped[str | None] = mapped_column(String(64), default=None, nullable=True, comment="失败码")
    created_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False, comment="创建时间"
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, comment="可查询期限（默认 24 小时）"
    )


class SystemPromptTemplateModel(ModelMixin):
    """提示词模板（表 sbp_ai_system_prompt_template）：模板版本化，改版不覆盖历史。"""

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


class SystemAuthCacheModel(MappedBase):
    """授权缓存（表 sbp_ai_system_auth_cache，P1 可选）：共享表查询结果的短 TTL 快照。

    不承担降级职责：共享表不可用时直接 503（ADR-8），本表只减少重复计算。
    """

    __tablename__: str = "sbp_ai_system_auth_cache"
    __table_args__: tuple = (
        UniqueConstraint("scope_id", "user_id", name="uq_sbp_ai_system_auth_cache_scope_user"),
        Index("ix_sbp_ai_system_auth_cache_expires", "expires_at"),
        {"comment": "授权缓存（scope_id + user_id 快照，TTL ≤120 秒）"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="主键ID")
    scope_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="授权范围 ID（authorization.scope_id）")
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="用户（sbp_sys_user.id）")
    permission_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, nullable=False, comment="权限快照（部门集/团队集/可见集摘要）"
    )
    watermark: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, nullable=True, comment="共享表更新时间水位（未变则复用）"
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, comment="过期时间（≤120 秒）")
