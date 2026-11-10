"""契约路由公共出口：统一路由类 / 公共依赖 / 响应声明。

每个 controller 都用 :func:`make_contract_router` 构造 Router，保证 `/v1` 路由在
「审计（`ContractApiRoute`）+ 验签 + 授权时效 + 错误体声明」上完全一致；
新增接口只需在对应 controller 里加装饰器，公共口径不复制。

P6 起 20 条路由均已接入业务实现（v3 D06 切片查重补入后为 21 条），骨架期的 501 出口（``skeleton``）已移除；
下面 501 的响应声明保留，仅用于让调用方按同一套错误体处理预留状态。
"""

from typing import Any

from fastapi import APIRouter, Depends

from .deps import enforced_authorization, verified_signature
from .errors import ContractApiRoute, ContractErrorResponse

# 每个 /v1 操作都会出现的响应。写进 responses 才能在 Swagger 上看到错误体：
# 本组路由的错误体是裸 `{"error": {...}}`，不展示就只剩状态码，联调时无从对字段。
COMMON_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ContractErrorResponse, "description": "参数校验失败（`INVALID_REQUEST` / `SCOPE_REQUIRED`）"},
    401: {
        "model": ContractErrorResponse,
        "description": "签名缺失 / 不符 / 时间戳越窗 / nonce 重放 / 授权块过期（`UNAUTHORIZED`），带 `WWW-Authenticate`",
    },
    403: {"model": ContractErrorResponse, "description": "无访问权限或治理范围失效（`CONTEXT_OUT_OF_SCOPE`）"},
    404: {"model": ContractErrorResponse, "description": "资源不存在（`NOT_FOUND`）"},
    409: {
        "model": ContractErrorResponse,
        "description": "幂等正文不一致 / 旧修订 / 来源变化 / 结果过期（`IDEMPOTENCY_CONFLICT` / `STALE_REVISION`）",
    },
    413: {"model": ContractErrorResponse, "description": "正文或资源清单超过容量（`PAYLOAD_TOO_LARGE`）"},
    # FastAPI 有请求体/路径参数时默认会记一笔 422。本组路由已把参数校验归一成 400，
    # 这里占位改写描述，避免联调方按 422 写错误分支。
    422: {
        "model": ContractErrorResponse,
        "description": "本组**不会返回 422**：参数校验失败已归一为 400 `INVALID_REQUEST`（本条仅用于抑制默认说明）",
    },
    429: {"model": ContractErrorResponse, "description": "触发限流（`RATE_LIMITED`，`retryable=true`，带 `Retry-After`）"},
    501: {
        "model": ContractErrorResponse,
        "description": "预留状态：错误体与其它状态码同构，本期 21 条路由均已实现，不再返回",
    },
    503: {"model": ContractErrorResponse, "description": "依赖不可用，如权限数据 / 防重放台账（`AUTHORIZATION_UNAVAILABLE`）"},
}


def make_contract_router(*, prefix: str, tags: list[str]) -> APIRouter:
    """构造契约 Router：路由类、验签依赖与错误响应声明一次配齐。"""
    return APIRouter(
        prefix=prefix,
        tags=tags,
        route_class=ContractApiRoute,
        dependencies=[Depends(verified_signature), Depends(enforced_authorization)],
        responses=COMMON_RESPONSES,
    )
