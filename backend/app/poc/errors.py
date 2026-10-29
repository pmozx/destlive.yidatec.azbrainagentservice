"""`/v1` 统一错误体与错误码（POC 版）：`{"error": {"code", "message", "retryable"}}`。

口径（手册「错误码说明」章节）：
1. AI 返回裸 JSON，不套 GSI 的 `code/msg/data` 外层 —— 所以 `/v1` 必须在**路由层**把异常换成
   契约错误体；基座 `handle_exception` 的 `ResponseSchema` 只服务 `/api/v1`，不能复用；
2. 错误码取手册出现过的 10 个业务码（小写），未定义的细分场景（模型服务不可用、授权块过期等）
   按最接近的 HTTP 类别归口，并在 T0.4 契约疑点清单里登记待 GSI 确认；
3. `retryable` 只在「同 ID 同正文重试有机会成功」时为 true：4xx 语义错误一律 false，
   5xx/超时/外部模型服务不可用为 true。

实现方式是路由类（`PocApiRoute`）包一层 try/except：不改基座的全局异常处理器，
避免 `/api/v1` 的响应体被 POC 规则污染。
"""

import json
from typing import Any

from fastapi import Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException

from app.core.exceptions import CustomException
from app.core.logger import logger

# 手册出现过的业务码（T1.2 会补全错误码常量表与映射；POC 只覆盖用得到的）
INVALID_REQUEST = "invalid_request"
UNAUTHORIZED = "unauthorized"
CONTEXT_OUT_OF_SCOPE = "context_out_of_scope"
NOT_FOUND = "not_found"
IDEMPOTENCY_CONFLICT = "idempotency_conflict"
STALE_REVISION = "stale_revision"
PAYLOAD_TOO_LARGE = "payload_too_large"
RATE_LIMITED = "rate_limited"
AUTHORIZATION_UNAVAILABLE = "authorization_unavailable"
INTERNAL_ERROR = "internal_error"
# 手册正文提到但未进错误码表：FAQ/stream 等场景的细分码（POC 按原文使用）
UNSUPPORTED_STREAM = "unsupported_stream"
PARSE_FAILED = "parse_failed"


class PocErrorDetail(BaseModel):
    """契约错误体的 `error` 对象。"""

    code: str = Field(description="契约错误码（小写），如 `invalid_request`")
    message: str = Field(description="可直接展示给调用方的说明，不回显期望值")
    retryable: bool = Field(description="同 ID 同正文重试是否有机会成功")


class PocErrorResponse(BaseModel):
    """`/v1` 错误体（**仅用于 OpenAPI/Swagger 展示**；实际响应由 `_response()` 直接序列化）。"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "error": {"code": "unauthorized", "message": "签名校验失败：X-AI-Signature 缺失。", "retryable": False},
                "request_id": "request-001",
            }
        }
    )

    error: PocErrorDetail
    request_id: str | None = Field(default=None, description="能识别请求时原样带回（写操作即幂等键）")


class PocApiError(Exception):
    """`/v1` 业务异常：携带契约错误码与 HTTP 状态，由 `PocApiRoute` 转成统一错误体。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        retryable: bool = False,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable
        self.request_id = request_id


def error_body(
    code: str,
    message: str,
    *,
    retryable: bool = False,
    request_id: str | None = None,
) -> dict[str, Any]:
    """契约错误体；`request_id` 能识别请求时原样带回，否则省略。"""
    body: dict[str, Any] = {"error": {"code": code, "message": message, "retryable": retryable}}
    if request_id:
        body["request_id"] = request_id
    return body


def _response(exc: PocApiError) -> Response:
    payload = error_body(exc.code, exc.message, retryable=exc.retryable, request_id=exc.request_id)
    return Response(
        content=json.dumps(payload, ensure_ascii=False),
        status_code=exc.status_code,
        media_type="application/json",
        headers={"WWW-Authenticate": 'HMAC realm="ai-service"'} if exc.status_code == 401 else None,
    )


def _from_validation_error(exc: RequestValidationError, request_id: str | None) -> PocApiError:
    """参数校验失败 → 400 invalid_request（契约用 400，不用 FastAPI 默认的 422）。"""
    errors = exc.errors()
    if errors:
        first = errors[0]
        # loc 形如 ("body", "options", "chunk")，拼成 options.chunk 便于 GSI 定位
        location = ".".join(str(part) for part in first.get("loc", ()) if part != "body")
        message = f"{location} 无效：{first.get('msg', '参数校验失败')}" if location else str(first.get("msg", "参数校验失败"))
    else:
        message = "参数无效，请检查必填字段。"
    return PocApiError(INVALID_REQUEST, message, status_code=status.HTTP_400_BAD_REQUEST, request_id=request_id)


def _from_custom_exception(exc: CustomException) -> PocApiError:
    """基座/客户端异常归一：4xx 保留语义，5xx 与超时归为可重试的 internal_error。

    POC 只有一类 5xx 是"真不可用"（模型服务地址未配、向量化/生成超时），契约没有对应业务码，
    统一给 `internal_error` + retryable=true，待 GSI 确认后再细分。
    """
    code = INVALID_REQUEST if 400 <= exc.status_code < 500 else INTERNAL_ERROR
    retryable = exc.status_code >= 500
    return PocApiError(code, exc.msg, status_code=exc.status_code, retryable=retryable)


def _from_http_exception(exc: HTTPException) -> PocApiError:
    if exc.status_code == status.HTTP_404_NOT_FOUND:
        return PocApiError(NOT_FOUND, "请求的资源不存在或不可访问。", status_code=exc.status_code)
    if exc.status_code == status.HTTP_405_METHOD_NOT_ALLOWED:
        return PocApiError(INVALID_REQUEST, "请求方法不被支持。", status_code=exc.status_code)
    return PocApiError(INTERNAL_ERROR, str(exc.detail or "服务内部错误"), status_code=exc.status_code)


def _extract_request_id(body: bytes) -> str | None:
    """从原始正文里尽力取 request_id（错误响应回带用；解析失败不影响主流程）。"""
    try:
        data = json.loads(body) if body else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    value = data.get("request_id") if isinstance(data, dict) else None
    return str(value) if isinstance(value, str) and value else None


def _failed(request: Request, error: PocApiError) -> Response:
    """错误响应：先标记录本次请求失败，再由 `poc_db` 回滚整个请求事务。

    FastAPI 的依赖退出代码在响应发出之后才执行，错误体又已经被本路由类接住（异常不再上抛），
    不标记的话请求级的数据库事务会被当成成功提交，把半成品写入落库。
    """
    request.state.poc_failed = True
    logger.warning("[POC /v1] {} {} | {} | {}", request.method, request.url.path, error.code, error.message)
    return _response(error)


class PocApiRoute(APIRoute):
    """`/v1` 路由类：把异常统一换成契约错误体（与 `ApiLogRoute` 同构，只换处理目标）。"""

    def get_route_handler(self):
        original_route_handler = super().get_route_handler()

        async def custom_route_handler(request: Request) -> Response:
            # 审计/日志需要的原始正文：Starlette 会缓存 body，下游依赖可再次读取
            body = await request.body()
            try:
                return await original_route_handler(request)
            except PocApiError as exc:
                return _failed(request, exc)
            except RequestValidationError as exc:
                return _failed(request, _from_validation_error(exc, _extract_request_id(body)))
            except CustomException as exc:
                return _failed(request, _from_custom_exception(exc))
            except HTTPException as exc:
                return _failed(request, _from_http_exception(exc))
            except Exception as exc:
                logger.exception("[POC /v1] {} {} 未捕获异常: {}", request.method, request.url.path, exc)
                return _failed(
                    request,
                    PocApiError(
                        INTERNAL_ERROR,
                        "服务内部处理失败，请按 retryable 判断是否重试。",
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        retryable=True,
                        request_id=_extract_request_id(body),
                    ),
                )

        return custom_route_handler
