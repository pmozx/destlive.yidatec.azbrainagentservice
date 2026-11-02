"""接口审计日志：路由级采集 + 后台异步落库（落表 sbp_ai_system_api_log）。

用法：``APIRouter(route_class=ApiLogRoute, ...)``，整组接口即获得审计能力，业务代码零侵入。

骨架沿用基座后端的 OperationLogRoute（方法白名单 + 字段脱敏 + 独立会话落库），
针对轻量应用补了三处缺口：
1. 业务异常同样留痕：原实现 ``await handler()`` 在 try 之外，抛异常（400/401/403/校验失败）的请求一条都不落，
   而这类请求恰恰是审计最需要的；
2. 操作人可回填：原实现读 ``request.state.ctx`` 但全链路无人写入，username 恒为 unknown；这里提供
   ``set_audit_username`` 供认证依赖/中间件回填；
3. 落库统一走独立任务，不再写 ``response.background``：既不会覆盖业务自身的后台任务，也避开
   「请求事务待提交 / 后台任务待写锁」在 SQLite 单写者下互等的问题。

契约接入层（T1.4）另补两处：
4. 安全校验留痕：``set_audit_security`` 写入 ``auth_mode`` / ``client_id`` / ``request_id`` /
   ``security_check(JSONB)``，由 ``/v1`` 的验签依赖在失败点逐步回填，被拒请求也能落审计；
5. ``status`` 按响应码判定（<400 成功、≥400 失败），不再把 4xx 记成成功。
"""

import asyncio
import json
import time
from collections.abc import Callable, Coroutine
from typing import Any, TypedDict

from fastapi import Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute

from app.config.setting import settings
from app.core.database import async_session_factory
from app.core.logger import logger

# 键名命中即脱敏：登录、令牌、第三方密钥等一律不入库明文
_SENSITIVE_KEYS: set[str] = {
    "password",
    "passwd",
    "old_password",
    "new_password",
    "confirm_password",
    "token",
    "access_token",
    "refresh_token",
    "authorization",
    "api_key",
    "apikey",
    "secret",
    "client_secret",
    "secret_key",
}
_REDACTED: str = "******"
_ANONYMOUS: str = "anonymous"


def _redact_sensitive(obj: Any) -> Any:
    """递归脱敏 dict/list 中的敏感字段（键名匹配不区分大小写）。"""
    if isinstance(obj, dict):
        return {k: (_REDACTED if str(k).lower() in _SENSITIVE_KEYS else _redact_sensitive(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact_sensitive(item) for item in obj]
    return obj


def _truncate(text: str, limit: int) -> str:
    """超长文本截断，保留前缀便于回溯定位。"""
    if limit <= 0 or len(text) <= limit:
        return text
    return f"{text[:limit]}...(已截断, 原长 {len(text)} 字符)"


def _get_client_ip(request: Request) -> str | None:
    """取客户端 IP：默认只认 TCP 对端地址，代理头仅在配置了可信跳数后才使用。"""
    peer = request.client.host if request.client else None
    hops = settings.TRUSTED_PROXY_HOPS
    if hops <= 0:
        return peer

    real_ip = request.headers.get("X-Real-IP", "").strip()
    if real_ip:
        return real_ip

    forwarded = [p.strip() for p in request.headers.get("X-Forwarded-For", "").split(",") if p.strip()]
    if forwarded:
        # 每层代理向右追加一跳，取倒数第 hops 个才是真实客户端
        return forwarded[-min(hops, len(forwarded))]
    return peer


def set_audit_username(request: Request, username: str | None) -> None:
    """回填本次请求的操作人：认证依赖/中间件解析出用户后调用一次即可。"""
    request.state.audit_username = username


def _resolve_username(request: Request) -> str:
    """操作人优先级：request.state 回填 > 配置的请求头 > anonymous。"""
    username = getattr(request.state, "audit_username", None)
    if not username and settings.API_LOG_USER_HEADER:
        username = request.headers.get(settings.API_LOG_USER_HEADER)
    return str(username or _ANONYMOUS)[:64]


def set_audit_security(
    request: Request,
    *,
    auth_mode: str | None = None,
    client_id: str | None = None,
    request_id: str | None = None,
    security_check: dict[str, Any] | None = None,
) -> None:
    """回填本次请求的鉴权上下文：验签依赖每完成一步就调一次，失败也留下已知部分。

    四个字段直接对应 ``sbp_ai_system_api_log`` 的扩展列（T1.0 已建），这里只负责放到
    ``request.state``，真正的落库由 ``_build_log_data`` 统一取值，业务代码零侵入。
    """
    request.state.audit_auth_mode = auth_mode
    request.state.audit_client_id = client_id
    request.state.audit_request_id = request_id
    request.state.audit_security_check = security_check


class ApiLogRecord(TypedDict):
    """审计落库记录（字段与 SystemApiLogModel 一一对应）。"""

    username: str
    status: int
    description: str
    request_path: str
    request_method: str
    request_query: str | None
    request_payload: str | None
    response_code: int
    response_json: str | None
    process_time: str
    request_ip: str | None
    user_agent: str | None
    auth_mode: str | None
    client_id: str | None
    request_id: str | None
    security_check: dict[str, Any] | None


# 落库任务持强引用防 GC 提前回收（事件循环只持有弱引用）
_pending_tasks: set[asyncio.Task[None]] = set()


async def _write_api_log_async(log_data: ApiLogRecord) -> None:
    """独立会话落库：不复用请求级会话（响应返回时该事务已结束）。"""
    from app.modules.system.api_log.model import SystemApiLogModel  # 延迟导入：core 不反向依赖业务层

    try:
        async with async_session_factory() as session:
            session.add(SystemApiLogModel(**log_data))
            await session.commit()
    except Exception:
        logger.exception("接口审计日志写入失败: path={}", log_data.get("request_path"))


def _schedule_write(log_data: ApiLogRecord) -> None:
    """异步落库（不阻塞响应），写失败只记应用日志。"""
    task = asyncio.create_task(_write_api_log_async(log_data))
    _pending_tasks.add(task)
    task.add_done_callback(_pending_tasks.discard)


async def flush_pending_api_logs(timeout: float = 5.0) -> int:
    """等待在途审计写入结束：供应用关停时调用，避免连接池被提前释放导致日志丢失。"""
    pending = [task for task in _pending_tasks if not task.done()]
    if not pending:
        return 0
    await asyncio.wait(pending, timeout=timeout)
    return len(pending)


async def _extract_request_payload(request: Request) -> str | None:
    """采集请求参数：JSON 复用已解析结果，表单/文件**只记元信息**。

    两条改进（原来这里对每个请求都重新 `json.loads`，且对 multipart 调 `request.form()`）：
    1. JSON 正文走 ``app.core.request_body`` 的缓存，验签/幂等/审计共用一次反序列化；
    2. 表单不再主动解析：``request.form()`` 会把上传文件整个读进内存，
       审计只需知道「传了几个字段、多大」，没被业务读过的表单不值得为它付内存峰值。
    """
    from app.core.request_body import parse_json_bytes, peek_json_body

    oper_param: dict[str, Any] = {}
    content_type = request.headers.get("Content-Type", "")
    try:
        if content_type.startswith(("multipart/form-data", "application/x-www-form-urlencoded")):
            # 业务已解析过表单时 Starlette 会缓存，直接复用；没解析过就不在这里触发解析
            cached_form = getattr(request, "_form", None)
            if cached_form is not None:
                oper_param["form"] = {
                    k: (_REDACTED if str(k).lower() in _SENSITIVE_KEYS else v)
                    for k, v in cached_form.items()
                    if not hasattr(v, "read")  # 过滤 UploadFile，避免把文件内容写进日志
                }
            else:
                oper_param["form"] = {
                    "content_type": content_type,
                    "content_length": request.headers.get("Content-Length"),
                    "note": "表单未由业务解析，审计仅记录元信息（不把上传体读进内存）",
                }
        else:
            payload = peek_json_body(request)
            raw = b""
            if payload is None:
                raw = await request.body()
                payload = parse_json_bytes(raw)
            if payload is not None:
                oper_param["body"] = _redact_sensitive(payload)
            elif raw:
                oper_param["body"] = raw.decode("utf-8", errors="ignore")
    except Exception:
        # 请求体已被下游流式消费等场景拿不到原文，不影响审计主流程
        oper_param["body"] = "<请求体不可读>"

    if request.query_params:
        oper_param["query"] = dict(request.query_params)
    if request.path_params:
        oper_param["path_params"] = dict(request.path_params)
    if not oper_param:
        return None
    return _truncate(json.dumps(oper_param, ensure_ascii=False), settings.API_LOG_MAX_PAYLOAD)


def _extract_response_json(response: Response) -> str | None:
    """采集响应体：仅处理 JSON，流式/文件响应不读（避免整包进内存）。"""
    if "application/json" not in response.headers.get("Content-Type", ""):
        return None
    body = getattr(response, "body", None)  # StreamingResponse 无 body 属性
    if not body:
        return None
    try:
        data = _redact_sensitive(json.loads(bytes(body).decode()))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return _truncate(json.dumps(data, ensure_ascii=False), settings.API_LOG_MAX_PAYLOAD)


def _route_summary(request: Request) -> str:
    """接口描述取自 controller 的 summary，作为“操作名称”。"""
    route = request.scope.get("route")
    return str(getattr(route, "summary", "") or "")


def _should_record(request: Request) -> bool:
    """过滤：总开关、文档/静态资源路径、请求方法白名单（契约 `/v1` 不受方法白名单限制）。"""
    if not settings.API_LOG_ENABLE:
        return False
    path = request.url.path
    if any(path.startswith(prefix) for prefix in settings.API_LOG_SKIP_PATHS):
        return False
    if request.method in settings.API_LOG_RECORD_METHOD:
        return True
    # 契约口径（T1.4）：/v1 的 20 个接口全量留痕，#02 这类 GET 轮询不能因为方法白名单被过滤掉
    return any(path.startswith(prefix) for prefix in settings.API_LOG_FORCE_PATHS)


async def _build_log_data(
    request: Request,
    *,
    status: int,
    response_code: int,
    description: str,
    response_json: str | None,
    start: float,
) -> ApiLogRecord:
    """组装一条审计记录（耗时单位统一为毫秒）。"""
    return {
        "username": _resolve_username(request),
        "status": status,
        "description": description,
        "request_path": request.url.path,
        "request_method": request.method,
        "request_query": _truncate(request.url.query, settings.API_LOG_MAX_PAYLOAD) or None,
        "request_payload": await _extract_request_payload(request),
        "response_code": response_code,
        "response_json": response_json,
        "process_time": f"{(time.perf_counter() - start) * 1000:.1f}ms",
        "request_ip": _get_client_ip(request),
        "user_agent": (request.headers.get("User-Agent") or None),
        # 鉴权四字段：/api/v1（JWT）与 /v1（HMAC）同表落库，来源是 set_audit_security 的 state 回填
        "auth_mode": getattr(request.state, "audit_auth_mode", None),
        "client_id": getattr(request.state, "audit_client_id", None),
        "request_id": getattr(request.state, "audit_request_id", None),
        "security_check": getattr(request.state, "audit_security_check", None),
    }


async def _record_success(request: Request, response: Response, start: float) -> None:
    """正常返回：派发独立任务落库，不增加接口耗时、不改动响应的 background。"""
    try:
        log_data = await _build_log_data(
            request,
            # 4xx 是「被拒绝的请求」，审计口径属失败；只有 2xx/3xx 才算成功
            status=0 if response.status_code < 400 else 1,
            response_code=response.status_code,
            description=_route_summary(request),
            response_json=_extract_response_json(response),
            start=start,
        )
    except Exception:
        logger.warning("接口审计日志采集失败: {}", request.url.path, exc_info=True)
        return

    _schedule_write(log_data)


async def _record_failure(request: Request, exc: Exception, start: float) -> None:
    """异常返回：同样留痕（status=1），随后由上层异常处理器决定响应体。"""
    try:
        status_code = getattr(exc, "status_code", 500)
        response_code = status_code if isinstance(status_code, int) else 500
        if isinstance(exc, RequestValidationError):
            # FastAPI 的参数校验异常状态码是 422，但 /v1 契约把校验失败归一成 400，审计须与响应一致
            response_code = 400
        message = str(getattr(exc, "msg", None) or exc or type(exc).__name__)
        summary = _route_summary(request)
        log_data = await _build_log_data(
            request,
            status=1,
            response_code=response_code,
            description=f"{summary} | {message}" if summary else message,
            response_json=_truncate(json.dumps({"error": message}, ensure_ascii=False), settings.API_LOG_MAX_PAYLOAD),
            start=start,
        )
        _schedule_write(log_data)
    except Exception:
        logger.warning("接口审计日志异常路径采集失败: {}", request.url.path, exc_info=True)


class ApiLogRoute(APIRoute):
    """审计路由类 — 按配置的请求方法自动记录请求/响应并后台异步写入。"""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original_route_handler = super().get_route_handler()

        async def custom_route_handler(request: Request) -> Response:
            if not _should_record(request):
                return await original_route_handler(request)

            start = time.perf_counter()
            try:
                response = await original_route_handler(request)
            except Exception as exc:
                await _record_failure(request, exc, start)
                raise

            await _record_success(request, response, start)
            return response

        return custom_route_handler
