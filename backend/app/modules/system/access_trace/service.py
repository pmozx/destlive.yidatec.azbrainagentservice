"""接入调用追踪服务：四张台账的只读分页 + request_id 时间线 + 近 N 小时概览。

数据访问统一走 ``crud.py`` 的 CRUD 封装（复用 ``CRUDBase`` 的条件 / 排序 / 分页语义）：
- 普通列条件由 ``search_to_dict`` 生成后直接下传；
- JSONB 子字段与幂等业务键前缀匹配通过 ``extra_conditions`` 追加；
- 本层不再手写 COUNT / 分页 / 排序 SQL，避免与 ``CRUDBase`` 两套口径漂移。

只读原则：不提供删除/清空。nonce 与幂等台账按 TTL 由应用启动自检清理，人工删行会让"防重放"
和"重试回放首次结果"失真（删过的 nonce 立刻可被重放，删过的幂等键会重复执行）。
"""

from datetime import UTC, datetime, timedelta
from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_schema import PageResultSchema
from app.core.exceptions import CustomException
from app.utils.common_util import search_to_dict

from .crud import (
    SystemApiLogCRUD,
    SystemIdempotencyCRUD,
    SystemNonceCRUD,
    SystemRequestLogCRUD,
)
from .model import (
    SystemApiLogModel,
    SystemIdempotencyModel,
    SystemNonceModel,
    SystemRequestLogModel,
)
from .schema import (
    AccessTraceDetailOutSchema,
    AccessTraceOutSchema,
    AccessTraceQueryParam,
    IdempotencyOutSchema,
    IdempotencyQueryParam,
    NonceOutSchema,
    NonceQueryParam,
    RequestLogOutSchema,
    RequestLogQueryParam,
    TraceSummarySchema,
    TraceTimelineSchema,
)

# 契约路径前缀：概览只统计 GSI 走的 /v1，不混入管理端 /api/v1 自身调用
CONTRACT_PATH_PREFIX = "/v1/%"
# 单个 request_id 的追踪结果上限：正常一次调用不超过 5 行，异常 ID 也不能把库拖住
TIMELINE_LIMIT = 20


class AccessTraceService:
    """接入调用追踪（只读）"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.calls = SystemApiLogCRUD(db)
        self.nonces = SystemNonceCRUD(db)
        self.idempotency = SystemIdempotencyCRUD(db)
        self.request_logs = SystemRequestLogCRUD(db)

    # ── 调用明细（sbp_ai_system_api_log）──────────────────────────────

    async def page_calls(
        self,
        page_no: int,
        page_size: int,
        search: AccessTraceQueryParam | None = None,
        order_by: list[dict[str, str]] | None = None,
    ) -> PageResultSchema[AccessTraceOutSchema]:
        """分页查询契约调用；签名结果与 nonce 状态走 JSONB 子字段过滤。"""
        data = search_to_dict(search) or {}
        signature_result = data.pop("signature_result", None)
        nonce_state = data.pop("nonce_state", None)

        # 空串会被解析成 like ''（命中全表），先剔除再决定是否走默认口径
        request_path = data.get("request_path")
        if isinstance(request_path, tuple) and not request_path[1]:
            data.pop("request_path", None)
            request_path = None

        extra = []
        # 页面口径是「GSI 契约调用」：不传路径时默认只看 /v1；显式传 request_path 则按用户条件查（可回溯管理台路径）
        if not request_path:
            extra.append(SystemApiLogModel.request_path.like(CONTRACT_PATH_PREFIX))
        if signature_result:
            extra.append(SystemApiLogModel.security_check["signature_result"].astext == signature_result)
        if nonce_state:
            extra.append(SystemApiLogModel.security_check["nonce_state"].astext == nonce_state)

        return await self.calls.page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=order_by or [{"id": "desc"}],
            search=data,
            out_schema=AccessTraceOutSchema,
            extra_conditions=extra,
        )

    async def call_detail(self, id: int) -> AccessTraceDetailOutSchema:
        """单条调用详情（含请求/响应体）。"""
        detail = await self.calls.get_or_404(
            id=id,
            msg="调用记录不存在",
            out_schema=AccessTraceDetailOutSchema,
        )
        return cast(AccessTraceDetailOutSchema, detail)

    # ── 防重放（sbp_ai_system_nonce）──────────────────────────────────

    async def page_nonces(
        self,
        page_no: int,
        page_size: int,
        search: NonceQueryParam | None = None,
        order_by: list[dict[str, str]] | None = None,
    ) -> PageResultSchema[NonceOutSchema]:
        """分页查询 nonce 登记（一条 = 一次首用；重放不会新增行）。"""
        return await self.nonces.page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=order_by or [{"id": "desc"}],
            search=search_to_dict(search),
            out_schema=NonceOutSchema,
        )

    # ── 幂等台账（sbp_ai_system_idempotency）──────────────────────────
    # 这张表解释 GSI 重试的两种回执：拿回首次结果（replay）或 409（异正文/处理中），
    # 以及删除类墓碑（expires_at 为空，永不清理）；biz_id 是业务键，不等于 request_id。

    async def page_idempotency(
        self,
        page_no: int,
        page_size: int,
        search: IdempotencyQueryParam | None = None,
        order_by: list[dict[str, str]] | None = None,
    ) -> PageResultSchema[IdempotencyOutSchema]:
        """分页查询幂等台账。"""
        return await self.idempotency.page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=order_by or [{"id": "desc"}],
            search=search_to_dict(search),
            out_schema=IdempotencyOutSchema,
        )

    # ── 结果台账（sbp_ai_system_request_log）──────────────────────────

    async def page_request_logs(
        self,
        page_no: int,
        page_size: int,
        search: RequestLogQueryParam | None = None,
        order_by: list[dict[str, str]] | None = None,
    ) -> PageResultSchema[RequestLogOutSchema]:
        """分页查询超时结果台账。"""
        return await self.request_logs.page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=order_by or [{"id": "desc"}],
            search=search_to_dict(search),
            out_schema=RequestLogOutSchema,
        )

    # ── 追踪与概览 ────────────────────────────────────────────────────

    async def timeline(self, request_id: str) -> TraceTimelineSchema:
        """按 request_id 把四张表串起来：调用 → 防重放 → 幂等 → 结果。"""
        rid = (request_id or "").strip()
        if not rid:
            raise CustomException(msg="请填写要追踪的 request_id")

        calls = await self.calls.get_list(
            search={"request_id": ("eq", rid)},
            order_by=[{"id": "desc"}],
            limit=TIMELINE_LIMIT,
        )
        nonces = await self.nonces.get_list(
            search={"request_id": ("eq", rid)},
            order_by=[{"id": "desc"}],
            limit=TIMELINE_LIMIT,
        )
        idempotency = await self.idempotency.get_by_request_id(rid, TIMELINE_LIMIT)
        request_logs = await self.request_logs.get_list(
            search={"request_id": ("eq", rid)},
            order_by=[{"id": "desc"}],
            limit=TIMELINE_LIMIT,
        )

        return TraceTimelineSchema(
            request_id=rid,
            found=bool(calls or nonces or idempotency or request_logs),
            calls=[AccessTraceOutSchema.model_validate(row) for row in calls],
            nonces=[NonceOutSchema.model_validate(row) for row in nonces],
            idempotency=[IdempotencyOutSchema.model_validate(row) for row in idempotency],
            request_logs=[RequestLogOutSchema.model_validate(row) for row in request_logs],
        )

    async def summary(self, hours: int = 24) -> TraceSummarySchema:
        """近 N 小时运维概览：只看 `/v1/*`，四张表各取关键计数。"""
        cutoff = datetime.now(UTC) - timedelta(hours=hours)
        window = [SystemApiLogModel.created_time >= cutoff, SystemApiLogModel.request_path.like(CONTRACT_PATH_PREFIX)]

        return TraceSummarySchema(
            window_hours=hours,
            call_total=await self.calls.count(extra_conditions=window),
            call_failed=await self.calls.count(
                extra_conditions=[*window, SystemApiLogModel.status == 1]
            ),
            signature_failed=await self.calls.count(
                extra_conditions=[*window, SystemApiLogModel.security_check["signature_result"].astext == "failed"]
            ),
            nonce_reused=await self.calls.count(
                extra_conditions=[*window, SystemApiLogModel.security_check["nonce_state"].astext == "reused"]
            ),
            idempotency_conflict=await self.calls.count(
                extra_conditions=[*window, SystemApiLogModel.response_code == 409]
            ),
            nonce_registered=await self.nonces.count(
                extra_conditions=[SystemNonceModel.created_time >= cutoff]
            ),
            request_running=await self.request_logs.count(
                extra_conditions=[SystemRequestLogModel.status == "running"]
            ),
            idempotency_processing=await self.idempotency.count(
                extra_conditions=[SystemIdempotencyModel.status == "processing"]
            ),
            idempotency_tombstone=await self.idempotency.count(
                extra_conditions=[SystemIdempotencyModel.status == "tombstone"]
            ),
        )
