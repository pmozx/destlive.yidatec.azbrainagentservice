"""操作日志审计：路由级采集 + 后台异步落库。

用法：``APIRouter(route_class=OperationLogRoute, ...)``，整组接口即获得审计能力，业务代码零侵入。

与 fastapi_admin_backend 的 OperationLogRoute 保持同一套骨架（方法白名单 + 字段脱敏 + 独立会话落库），
针对轻量应用补了三处缺口：
1. 业务异常同样留痕：原实现 ``await handler()`` 在 try 之外，抛异常（400/401/403/校验失败）的请求一条都不落，
   而这类请求恰恰是审计最需要的；
2. 操作人可回填：原实现读 ``request.state.ctx`` 但全链路无人写入，username 恒为 unknown；这里提供
   ``set_audit_username`` 供认证依赖/中间件回填；
3. 落库统一走独立任务，不再写 ``response.background``：既不会覆盖业务自身的后台任务，也避开
   「请求事务待提交 / 后台任务待写锁」在 SQLite 单写者下互等的问题。
"""

import asyncio
import json
import time
from collections.abc import Callable, Coroutine
from typing import Any, TypedDict

from fastapi import Request, Response
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
    if not username and settings.OPERATION_LOG_USER_HEADER:
        username = request.headers.get(settings.OPERATION_LOG_USER_HEADER)
    return str(username or _ANONYMOUS)[:64]


class OperationLogRecord(TypedDict):
    """审计落库记录（字段与 OperationLogModel 一一对应）。"""

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


# 落库任务持强引用防 GC 提前回收（事件循环只持有弱引用）
_pending_tasks: set[asyncio.Task[None]] = set()


async def _write_operation_log_async(log_data: OperationLogRecord) -> None:
    """独立会话落库：不复用请求级会话（响应返回时该事务已结束）。"""
    from app.api.v1.module_system.log.model import OperationLogModel  # 延迟导入：core 不反向依赖业务层

    try:
        async with async_session_factory() as session:
            session.add(OperationLogModel(**log_data))
            await session.commit()
    except Exception:
        logger.exception("操作日志写入失败: path={}", log_data.get("request_path"))


def _schedule_write(log_data: OperationLogRecord) -> None:
    """异步落库（不阻塞响应），写失败只记应用日志。"""
    task = asyncio.create_task(_write_operation_log_async(log_data))
    _pending_tasks.add(task)
    task.add_done_callback(_pending_tasks.discard)


async def flush_pending_writes(timeout: float = 5.0) -> int:
    """等待在途审计写入结束：供应用关停时调用，避免连接池被提前释放导致日志丢失。"""
    pending = [task for task in _pending_tasks if not task.done()]
    if not pending:
        return 0
    await asyncio.wait(pending, timeout=timeout)
    return len(pending)


async def _extract_request_payload(request: Request) -> str | None:
    """采集请求参数：表单/JSON/文本分支处理，文件对象丢弃，敏感字段脱敏。"""
    oper_param: dict[str, Any] = {}
    content_type = request.headers.get("Content-Type", "")
    try:
        if content_type.startswith(("multipart/form-data", "application/x-www-form-urlencoded")):
            form_data = await request.form()
            oper_param["form"] = {
                k: (_REDACTED if str(k).lower() in _SENSITIVE_KEYS else v)
                for k, v in form_data.items()
                if not hasattr(v, "read")  # 过滤 UploadFile，避免把文件内容写进日志
            }
        else:
            payload = await request.body()
            if payload:
                try:
                    oper_param["body"] = _redact_sensitive(json.loads(payload.decode()))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    oper_param["body"] = payload.decode("utf-8", errors="ignore")
    except Exception:
        # 请求体已被下游流式消费等场景拿不到原文，不影响审计主流程
        oper_param["body"] = "<请求体不可读>"

    if request.query_params:
        oper_param["query"] = dict(request.query_params)
    if request.path_params:
        oper_param["path_params"] = dict(request.path_params)
    if not oper_param:
        return None
    return _truncate(json.dumps(oper_param, ensure_ascii=False), settings.OPERATION_LOG_MAX_PAYLOAD)


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
    return _truncate(json.dumps(data, ensure_ascii=False), settings.OPERATION_LOG_MAX_PAYLOAD)


def _route_summary(request: Request) -> str:
    """接口描述取自 controller 的 summary，作为“操作名称”。"""
    route = request.scope.get("route")
    return str(getattr(route, "summary", "") or "")


def _should_record(request: Request) -> bool:
    """过滤：总开关、请求方法白名单、文档/静态资源路径。"""
    if not settings.OPERATION_LOG_ENABLE:
        return False
    if request.method not in settings.OPERATION_RECORD_METHOD:
        return False
    path = request.url.path
    return not any(path.startswith(prefix) for prefix in settings.OPERATION_LOG_SKIP_PATHS)


async def _build_log_data(
    request: Request,
    *,
    status: int,
    response_code: int,
    description: str,
    response_json: str | None,
    start: float,
) -> OperationLogRecord:
    """组装一条审计记录（耗时单位统一为毫秒）。"""
    return {
        "username": _resolve_username(request),
        "status": status,
        "description": description,
        "request_path": request.url.path,
        "request_method": request.method,
        "request_query": _truncate(request.url.query, settings.OPERATION_LOG_MAX_PAYLOAD) or None,
        "request_payload": await _extract_request_payload(request),
        "response_code": response_code,
        "response_json": response_json,
        "process_time": f"{(time.perf_counter() - start) * 1000:.1f}ms",
        "request_ip": _get_client_ip(request),
        "user_agent": (request.headers.get("User-Agent") or None),
    }


async def _record_success(request: Request, response: Response, start: float) -> None:
    """正常返回：派发独立任务落库，不增加接口耗时、不改动响应的 background。"""
    try:
        log_data = await _build_log_data(
            request,
            status=0 if response.status_code < 500 else 1,
            response_code=response.status_code,
            description=_route_summary(request),
            response_json=_extract_response_json(response),
            start=start,
        )
    except Exception:
        logger.warning("操作日志采集失败: {}", request.url.path, exc_info=True)
        return

    _schedule_write(log_data)


async def _record_failure(request: Request, exc: Exception, start: float) -> None:
    """异常返回：同样留痕（status=1），随后由上层异常处理器决定响应体。"""
    try:
        status_code = getattr(exc, "status_code", 500)
        response_code = status_code if isinstance(status_code, int) else 500
        message = str(getattr(exc, "msg", None) or exc or type(exc).__name__)
        summary = _route_summary(request)
        log_data = await _build_log_data(
            request,
            status=1,
            response_code=response_code,
            description=f"{summary} | {message}" if summary else message,
            response_json=_truncate(json.dumps({"error": message}, ensure_ascii=False), settings.OPERATION_LOG_MAX_PAYLOAD),
            start=start,
        )
        _schedule_write(log_data)
    except Exception:
        logger.warning("操作日志异常路径采集失败: {}", request.url.path, exc_info=True)


class OperationLogRoute(APIRoute):
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
