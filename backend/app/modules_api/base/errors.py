"""契约错误体与错误码全量表（T1.2）：`{"error": {"code", "message", "retryable"}}`。

口径（手册 `02-1` §七「业务错误码与处理方式」+ §八「重复治理」）：

1. AI 返回裸 JSON，不套 GSI 的 `code/msg/data` 外层 —— 所以 `/v1` 必须在**路由层**把异常换成
   契约错误体；基座 `handle_exception` 的 `ResponseSchema` 只服务 `/api/v1`，不能复用；
2. 错误码取值**大写下划线**，与手册 §七 表格、各接口「错误响应」示例逐字一致（`INVALID_REQUEST`），
   不是 POC 期自定的 `invalid_request`：契约比对是验收项，大小写不一致同样算差异；
3. 状态码由**错误码本身**推导（`ERROR_CODE_STATUS`），不允许同一码在不同接口变状态；
4. `retryable` 只在「同 ID 同正文重试有机会成功」时为 true：4xx 语义错误一律 false，
   429 / 503 / 5xx 为 true（手册 §七「仅当 retryable=true 时自动重试」）。

实现方式是路由类（`ContractApiRoute`）包一层 try/except，并**继承审计路由类**（T1.4）：
`ApiLogRoute` 在内层记录审计（含被拒请求），本类在外层把异常换成契约错误体，
两层职责不重叠；不改基座的全局异常处理器，避免 `/api/v1` 的响应体被契约规则污染。
"""

import json
from typing import Any

from fastapi import Request, Response, status
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException

from app.core.exceptions import CustomException
from app.core.logger import logger
from app.core.request_body import peek_json_body
from app.core.router_class import ApiLogRoute

from .audit import mark_failure

# ── 错误码（手册 §七 表格 + §八 细分码 + 任务失败码） ──────────────────────────
INVALID_REQUEST = "INVALID_REQUEST"
SCOPE_REQUIRED = "SCOPE_REQUIRED"
UNAUTHORIZED = "UNAUTHORIZED"
CONTEXT_OUT_OF_SCOPE = "CONTEXT_OUT_OF_SCOPE"
GOVERNANCE_SCOPE_DENIED = "GOVERNANCE_SCOPE_DENIED"
NOT_FOUND = "NOT_FOUND"
IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
STALE_REVISION = "STALE_REVISION"
SOURCE_CHANGED = "SOURCE_CHANGED"
RESULT_EXPIRED = "RESULT_EXPIRED"
PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
RATE_LIMITED = "RATE_LIMITED"
AUTHORIZATION_UNAVAILABLE = "AUTHORIZATION_UNAVAILABLE"
INTERNAL_ERROR = "INTERNAL_ERROR"
# #14 非流式承诺：stream=true 明确拒绝（手册 FR-RAG-04 / 参数手册 #14 stream 说明）
UNSUPPORTED_STREAM = "UNSUPPORTED_STREAM"
# 任务态失败码（`#02` 的 `error_code`，HTTP 仍是 200）——不是请求级错误体，但同属错误码表
PARSE_FAILED = "PARSE_FAILED"

# 码 → HTTP 状态：全量映射的唯一事实源，新增码只改这里
ERROR_CODE_STATUS: dict[str, int] = {
    INVALID_REQUEST: status.HTTP_400_BAD_REQUEST,
    SCOPE_REQUIRED: status.HTTP_400_BAD_REQUEST,
    UNAUTHORIZED: status.HTTP_401_UNAUTHORIZED,
    CONTEXT_OUT_OF_SCOPE: status.HTTP_403_FORBIDDEN,
    GOVERNANCE_SCOPE_DENIED: status.HTTP_403_FORBIDDEN,
    NOT_FOUND: status.HTTP_404_NOT_FOUND,
    IDEMPOTENCY_CONFLICT: status.HTTP_409_CONFLICT,
    STALE_REVISION: status.HTTP_409_CONFLICT,
    SOURCE_CHANGED: status.HTTP_409_CONFLICT,
    RESULT_EXPIRED: status.HTTP_409_CONFLICT,
    PAYLOAD_TOO_LARGE: status.HTTP_413_CONTENT_TOO_LARGE,
    RATE_LIMITED: status.HTTP_429_TOO_MANY_REQUESTS,
    AUTHORIZATION_UNAVAILABLE: status.HTTP_503_SERVICE_UNAVAILABLE,
    INTERNAL_ERROR: status.HTTP_500_INTERNAL_SERVER_ERROR,
    UNSUPPORTED_STREAM: status.HTTP_400_BAD_REQUEST,
    PARSE_FAILED: status.HTTP_200_OK,
}

# 默认可重试：重试有机会成功的三类（限流 / 依赖不可用 / 内部错误）；
# 其余 4xx 语义错误重试只会重复失败，默认 false，个别场景可显式覆盖
RETRYABLE_CODES: frozenset[str] = frozenset({RATE_LIMITED, AUTHORIZATION_UNAVAILABLE, INTERNAL_ERROR})

# HTTP 状态 → 兜底码：只用于「有状态、没指定契约码」的异常（基座 CustomException / HTTPException）
STATUS_DEFAULT_CODE: dict[int, str] = {
    status.HTTP_400_BAD_REQUEST: INVALID_REQUEST,
    status.HTTP_401_UNAUTHORIZED: UNAUTHORIZED,
    status.HTTP_403_FORBIDDEN: CONTEXT_OUT_OF_SCOPE,
    status.HTTP_404_NOT_FOUND: NOT_FOUND,
    status.HTTP_409_CONFLICT: IDEMPOTENCY_CONFLICT,
    status.HTTP_413_CONTENT_TOO_LARGE: PAYLOAD_TOO_LARGE,
    status.HTTP_429_TOO_MANY_REQUESTS: RATE_LIMITED,
    status.HTTP_503_SERVICE_UNAVAILABLE: AUTHORIZATION_UNAVAILABLE,
}

# 固定话术：不回显期望值、密钥与受限内容（手册「error.message 不泄漏密钥或受限内容」）
DEFAULT_MESSAGES: dict[str, str] = {
    INVALID_REQUEST: "请求参数无效，请检查必填字段和参数格式。",
    SCOPE_REQUIRED: "缺少权限范围信息。",
    UNAUTHORIZED: "服务签名认证失败，请检查签名、时间戳和 nonce。",
    CONTEXT_OUT_OF_SCOPE: "当前身份无权访问请求范围内的资源。",
    GOVERNANCE_SCOPE_DENIED: "治理范围已失效，请重新确认权限。",
    NOT_FOUND: "请求的资源不存在或不可访问。",
    IDEMPOTENCY_CONFLICT: "相同请求 ID 已用于不同的请求正文。",
    STALE_REVISION: "知识基线已发生变化，请读取最新内容后重新提交。",
    SOURCE_CHANGED: "来源内容已变化，请重新检测确认。",
    RESULT_EXPIRED: "结果已过期，请重新发起请求。",
    PAYLOAD_TOO_LARGE: "请求正文或资源清单超过允许的容量。",
    RATE_LIMITED: "请求过于频繁，请稍后使用原请求重试。",
    AUTHORIZATION_UNAVAILABLE: "权限数据暂不可用，请稍后重试。",
    INTERNAL_ERROR: "服务内部处理失败，请按 retryable 判断是否重试。",
    UNSUPPORTED_STREAM: "本期仅支持非流式生成，请将 stream 置为 false。",
    PARSE_FAILED: "无法解析文件。",
}


def code_for_status(status_code: int) -> str:
    """按 HTTP 状态兜底取契约码：未登记的状态一律 500 `INTERNAL_ERROR`。"""
    if status_code in STATUS_DEFAULT_CODE:
        return STATUS_DEFAULT_CODE[status_code]
    return INTERNAL_ERROR if status_code >= 500 else INVALID_REQUEST


class ContractErrorDetail(BaseModel):
    """契约错误体的 `error` 对象。"""

    code: str = Field(description="稳定的机器错误码（大写下划线），如 `INVALID_REQUEST`")
    message: str = Field(description="可直接展示给调用方的说明，不泄漏密钥或受限内容")
    retryable: bool = Field(description="同 ID 同正文重试是否有机会成功；重试须重新生成签名")


class ContractErrorResponse(BaseModel):
    """`/v1` 错误体（**仅用于 OpenAPI/Swagger 展示**；实际响应由 `_response()` 直接序列化）。"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "error": {
                    "code": UNAUTHORIZED,
                    "message": "服务签名认证失败，请检查签名、时间戳和 nonce。",
                    "retryable": False,
                },
                "request_id": "req-20261029-001",
            }
        }
    )

    error: ContractErrorDetail
    request_id: str | None = Field(default=None, description="能识别请求时原样带回（写操作即幂等键）")


class ContractApiError(Exception):
    """`/v1` 业务异常：只带契约码，HTTP 状态与 retryable 默认由码推导，由 `ContractApiRoute` 转成错误体。"""

    def __init__(
        self,
        code: str,
        message: str | None = None,
        *,
        status_code: int | None = None,
        retryable: bool | None = None,
        request_id: str | None = None,
    ) -> None:
        resolved_code = code if code in ERROR_CODE_STATUS else INTERNAL_ERROR
        resolved_status = status_code or ERROR_CODE_STATUS[resolved_code]
        super().__init__(message or DEFAULT_MESSAGES.get(resolved_code, resolved_code))
        self.code = resolved_code
        self.message = message or DEFAULT_MESSAGES.get(resolved_code, resolved_code)
        self.status_code = resolved_status
        self.retryable = resolved_code in RETRYABLE_CODES if retryable is None else retryable
        self.request_id = request_id


def error_body(
    code: str,
    message: str,
    *,
    retryable: bool = False,
    request_id: str | None = None,
) -> dict[str, Any]:
    """契约错误体；`request_id` 能识别请求时原样带回，否则省略（手册「认证失败可省略」）。"""
    body: dict[str, Any] = {"error": {"code": code, "message": message, "retryable": retryable}}
    if request_id:
        body["request_id"] = request_id
    return body


def _response(exc: ContractApiError) -> Response:
    """错误响应；401 带 `WWW-Authenticate`、429 带 `Retry-After`，便于网关与调用方分流处理。"""
    headers: dict[str, str] = {}
    if exc.status_code == status.HTTP_401_UNAUTHORIZED:
        headers["WWW-Authenticate"] = 'HMAC realm="ai-service"'
    elif exc.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
        headers["Retry-After"] = "1"
    return Response(
        content=json.dumps(
            error_body(exc.code, exc.message, retryable=exc.retryable, request_id=exc.request_id),
            ensure_ascii=False,
        ),
        status_code=exc.status_code,
        media_type="application/json",
        headers=headers or None,
    )


def _from_validation_error(exc: RequestValidationError, request_id: str | None) -> ContractApiError:
    """参数校验失败 → 400 `INVALID_REQUEST`（契约用 400，不用 FastAPI 默认的 422）。"""
    errors = exc.errors()
    if errors:
        first = errors[0]
        # loc 形如 ("body", "options", "chunk")，拼成 options.chunk 便于 GSI 定位
        location = ".".join(str(part) for part in first.get("loc", ()) if part != "body")
        message = f"{location} 无效：{first.get('msg', '参数校验失败')}" if location else str(first.get("msg", "参数校验失败"))
    else:
        message = DEFAULT_MESSAGES[INVALID_REQUEST]
    return ContractApiError(INVALID_REQUEST, message, request_id=request_id)


def _from_custom_exception(exc: CustomException) -> ContractApiError:
    """基座/客户端异常归一：保留 4xx 语义，5xx 与超时归为可重试的 `INTERNAL_ERROR`。"""
    code = code_for_status(exc.status_code)
    if exc.status_code >= 500:
        # 模型服务地址未配、向量化/生成超时等：契约里没有专用码，按 Q13 建议归 internal_error
        code = INTERNAL_ERROR
    return ContractApiError(code, exc.msg, status_code=exc.status_code)


def _from_http_exception(exc: HTTPException) -> ContractApiError:
    """Starlette/FastAPI 自身抛的 HTTPException（405、路由级 404 等）：按状态归口。"""
    message = str(exc.detail) if exc.status_code < 500 else DEFAULT_MESSAGES[INTERNAL_ERROR]
    return ContractApiError(code_for_status(exc.status_code), message, status_code=exc.status_code)


def _request_id_of(request: Request) -> str | None:
    """从**已缓存的**正文里取 request_id（错误响应回带用）。

    正文由验签依赖经 ``get_json_body`` 解析并缓存，这里只读缓存、不再反序列化；
    依赖没跑到的场景（路由级 404 等）拿不到正文，直接不回带。
    """
    data = peek_json_body(request)
    value = data.get("request_id") if isinstance(data, dict) else None
    return str(value) if isinstance(value, str) and value else None


def _failed(request: Request, error: ContractApiError) -> Response:
    """错误响应：先标记本次请求失败，供幂等依赖与请求级事务回滚判别。

    FastAPI 的依赖退出代码在响应发出之后才执行，错误体又已经被本路由类接住（异常不再上抛），
    不标记的话请求级数据库事务会被当成成功提交，把半成品写入落库。
    读取方：``app/core/dependencies.py::db_getter``（提交前检查该标记并回滚）
    与 ``app/modules_api/base/deps.py::idempotent_guard``（失败释放幂等预约）。
    """
    request.state.contract_failed = True
    # 签名通过后才失败的请求也要留下失败码，否则审计里只有 4xx 响应码、没有原因
    mark_failure(request, error.code)
    # 能识别请求就原样带回 request_id（手册 §七）；验签/授权阶段起的异常自身看不到正文
    if not error.request_id:
        error.request_id = _request_id_of(request)
    logger.warning("[contract /v1] {} {} | {} | {}", request.method, request.url.path, error.code, error.message)
    return _response(error)


class ContractApiRoute(ApiLogRoute):
    """`/v1` 路由类：内层继承审计（含被拒请求留痕），外层把异常统一换成契约错误体。"""

    def get_route_handler(self):
        # 内层已是 ApiLogRoute 的包装：异常先在那里落审计，再抛到本层换成错误体
        original_route_handler = super().get_route_handler()

        async def custom_route_handler(request: Request) -> Response:
            # 正文由验签依赖统一解析并缓存（见 app.core.request_body），这里不再重复读、重复解析
            try:
                return await original_route_handler(request)
            except ContractApiError as exc:
                return _failed(request, exc)
            except RequestValidationError as exc:
                return _failed(request, _from_validation_error(exc, _request_id_of(request)))
            except CustomException as exc:
                return _failed(request, _from_custom_exception(exc))
            except HTTPException as exc:
                return _failed(request, _from_http_exception(exc))
            except SQLAlchemyError as exc:
                # 数据库不可用/约束冲突等：不把驱动细节回给调用方
                logger.exception("[contract /v1] {} {} 数据库异常: {}", request.method, request.url.path, exc)
                return _failed(request, ContractApiError(INTERNAL_ERROR))
            except Exception as exc:
                logger.exception("[contract /v1] {} {} 未捕获异常: {}", request.method, request.url.path, exc)
                return _failed(request, ContractApiError(INTERNAL_ERROR, retryable=True))

        return custom_route_handler
