"""接入调用追踪（系统管理 → 接入调用追踪）：GSI 契约 `/v1` 调用的只读取证模型。

四张表的分工，页面按标签页一一对应：

- `sbp_ai_system_api_log` —— 每次调用一条（含被拒），`security_check` 存五头校验明细 → 「调用明细」；
- `sbp_ai_system_nonce` —— 防重放台账，只登记"首用"、重放不加行（重放证据在上一张表的 `nonce_state=reused`）→ 「防重放」；
- `sbp_ai_system_idempotency` —— 三类幂等域台账，解释"重试拿回首次结果"与 409 → 「幂等台账」；
- `sbp_ai_system_request_log` —— 超时后查询最终结果的台账，与幂等互补 → 「结果台账」。

**幂等台账为什么必须在页面里**：GSI 重试时要么拿回首次结果（replay），要么收到 409
`IDEMPOTENCY_CONFLICT`（同 ID 异正文）或"处理中"。没有这张表，运维看到 409 无法区分是重复提交、
正文不一致还是上次失败未释放；删除类墓碑（`expires_at` 为 NULL，永不清理）也只能在这里看到。
它的唯一键是 `(scope, biz_id)`，`biz_id` 是业务键（job_id / request_id / event_id），
与 `api_log.request_id` 不总是同源，因此按 request_id 追踪时用「精确匹配 + `request_id-` 前缀」补齐。
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.core.base_schema import BaseQueryParam
from app.core.validator import DateTimeStr


# ── 调用明细（sbp_ai_system_api_log）────────────────────────────────────


class AccessTraceOutSchema(BaseModel):
    """调用明细行：不返回请求/响应大字段（`response_json` 可达数 KB，列表页会拖垮传输）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="日志ID")
    created_time: DateTimeStr | None = Field(default=None, description="调用时间")
    username: str = Field(..., description="操作人（HMAC 调用为 client_id）")
    status: int = Field(default=0, description="操作状态(0:成功 1:失败)")
    description: str | None = Field(default=None, description="操作名称/异常摘要")
    request_path: str = Field(..., description="请求路径")
    request_method: str = Field(..., description="请求方式")
    response_code: int = Field(..., description="响应状态码")
    process_time: str | None = Field(default=None, description="处理耗时(毫秒)")
    request_ip: str | None = Field(default=None, description="来源 IP")
    auth_mode: str | None = Field(default=None, description="鉴权方式：jwt/hmac")
    client_id: str | None = Field(default=None, description="HMAC 调用方标识")
    request_id: str | None = Field(default=None, description="契约追踪 ID")
    security_check: dict[str, Any] | None = Field(default=None, description="安全头校验明细（含 failure_code）")


class AccessTraceDetailOutSchema(AccessTraceOutSchema):
    """调用详情：补请求/响应体与 UA，用于在页面上直接看原始报文。"""

    request_query: str | None = Field(default=None, description="查询字符串")
    request_payload: str | None = Field(default=None, description="请求参数")
    response_json: str | None = Field(default=None, description="响应体")
    user_agent: str | None = Field(default=None, description="浏览器 UA")


class AccessTraceQueryParam(BaseQueryParam):
    """调用明细查询参数（`q` 决定比较方式；JSONB 内部字段由 service 单独转条件）。"""

    request_id: str | None = Field(None, description="契约追踪 ID", json_schema_extra={"q": "like"})
    request_path: str | None = Field(
        None, description="请求路径；不传时只查 /v1 契约调用，传了则按条件查（可回溯 /api/v1）", json_schema_extra={"q": "like"}
    )
    request_method: str | None = Field(None, description="请求方式", json_schema_extra={"q": "eq"})
    client_id: str | None = Field(None, description="调用方标识", json_schema_extra={"q": "like"})
    auth_mode: str | None = Field(None, description="鉴权方式：jwt/hmac", json_schema_extra={"q": "eq"})
    status: int | None = Field(None, ge=0, le=1, description="操作状态(0:成功 1:失败)", json_schema_extra={"q": "eq"})
    response_code: int | None = Field(None, ge=100, le=599, description="响应状态码", json_schema_extra={"q": "eq"})
    signature_result: str | None = Field(
        None, description="签名结果：passed/failed/skipped（security_check 内部字段）", pattern="^(passed|failed|skipped)$"
    )
    nonce_state: str | None = Field(
        None, description="nonce 状态：first_use/reused/not_checked（security_check 内部字段）", pattern="^(first_use|reused|not_checked)$"
    )


# ── 防重放（sbp_ai_system_nonce）─────────────────────────────────────────


class NonceOutSchema(BaseModel):
    """防重放台账行：一条 = 一个 nonce 的首用登记。"""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="主键ID")
    client_id: str = Field(..., description="调用方标识")
    nonce: str = Field(..., description="nonce 原文")
    request_id: str | None = Field(default=None, description="关联请求（重放取证）")
    created_time: DateTimeStr | None = Field(default=None, description="首用时间")
    expires_at: DateTimeStr | None = Field(default=None, description="过期时间（过期后窗口外重放不再判重）")


class NonceQueryParam(BaseQueryParam):
    """防重放查询参数。"""

    client_id: str | None = Field(None, description="调用方标识", json_schema_extra={"q": "like"})
    nonce: str | None = Field(None, description="nonce（模糊）", json_schema_extra={"q": "like"})
    request_id: str | None = Field(None, description="关联请求 ID", json_schema_extra={"q": "like"})


# ── 幂等台账（sbp_ai_system_idempotency）────────────────────────────────


class IdempotencyOutSchema(BaseModel):
    """幂等台账行：`status` 为 processing/succeeded/failed/tombstone；墓碑的 `expires_at` 为 NULL。"""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="主键ID")
    scope: str = Field(..., description="幂等域：job/request/event")
    biz_id: str = Field(..., description="幂等键（job_id/request_id/event_id）")
    body_hash: str = Field(..., description="请求体 SHA-256")
    status: str = Field(..., description="台账状态")
    result_ref: dict[str, Any] | None = Field(default=None, description="首次结果摘要（回放返回）")
    created_time: DateTimeStr | None = Field(default=None, description="创建时间")
    expires_at: DateTimeStr | None = Field(default=None, description="保留到期（NULL=墓碑，永不清理）")


class IdempotencyQueryParam(BaseQueryParam):
    """幂等台账查询参数。"""

    scope: str | None = Field(None, description="幂等域：job/request/event", json_schema_extra={"q": "eq"})
    biz_id: str | None = Field(None, description="幂等键", json_schema_extra={"q": "like"})
    status: str | None = Field(None, description="台账状态", json_schema_extra={"q": "eq"})
    body_hash: str | None = Field(None, description="请求体摘要", json_schema_extra={"q": "like"})


# ── 结果台账（sbp_ai_system_request_log）────────────────────────────────


class RequestLogOutSchema(BaseModel):
    """结果台账行：超时后按 request_id 查询最终结果，过期返回 `RESULT_EXPIRED` 而非 404。"""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="主键ID")
    request_id: str = Field(..., description="契约请求 ID")
    operation: str = Field(..., description="completion/mutation/delete")
    status: str = Field(..., description="running/succeeded/failed/expired")
    result_visible: bool = Field(default=True, description="结果是否仍可返回")
    result_ref: dict[str, Any] | None = Field(default=None, description="结果引用")
    error_code: str | None = Field(default=None, description="失败码")
    created_time: DateTimeStr | None = Field(default=None, description="创建时间")
    expires_at: DateTimeStr | None = Field(default=None, description="可查询期限")


class RequestLogQueryParam(BaseQueryParam):
    """结果台账查询参数。"""

    request_id: str | None = Field(None, description="契约请求 ID", json_schema_extra={"q": "like"})
    operation: str | None = Field(None, description="completion/mutation/delete", json_schema_extra={"q": "eq"})
    status: str | None = Field(None, description="running/succeeded/failed/expired", json_schema_extra={"q": "eq"})
    error_code: str | None = Field(None, description="失败码", json_schema_extra={"q": "eq"})


# ── 追踪与汇总 ──────────────────────────────────────────────────────────


class TraceTimelineSchema(BaseModel):
    """按 request_id 把四张表串成一条时间线（每类最多 20 条，避免异常 ID 拖库）。"""

    request_id: str = Field(..., description="被追踪的追踪 ID")
    found: bool = Field(default=False, description="四张表里是否有任何命中")
    calls: list[AccessTraceOutSchema] = Field(default_factory=list, description="调用明细")
    nonces: list[NonceOutSchema] = Field(default_factory=list, description="防重放登记")
    idempotency: list[IdempotencyOutSchema] = Field(default_factory=list, description="幂等台账")
    request_logs: list[RequestLogOutSchema] = Field(default_factory=list, description="结果台账")


class TraceSummarySchema(BaseModel):
    """运维概览：只看契约路径（`/v1/*`），口径固定为"近 N 小时"。"""

    window_hours: int = Field(..., description="统计窗口（小时）")
    call_total: int = Field(default=0, description="契约调用总数")
    call_failed: int = Field(default=0, description="失败调用数（status=1）")
    signature_failed: int = Field(default=0, description="签名校验失败数")
    nonce_reused: int = Field(default=0, description="重放拦截数（nonce_state=reused）")
    idempotency_conflict: int = Field(default=0, description="幂等冲突数（响应 409）")
    nonce_registered: int = Field(default=0, description="窗口内登记的 nonce 数")
    request_running: int = Field(default=0, description="结果台账中在途（running）数")
    idempotency_processing: int = Field(default=0, description="幂等台账中处理中数")
    idempotency_tombstone: int = Field(default=0, description="删除类墓碑数（expires_at 为空）")
