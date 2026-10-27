from typing import Final
from app.core.logger import logger
from fastapi import FastAPI
from starlette.middleware.base import RequestResponseEndpoint, BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

"""
中间件 Middleware: 是一种在请求到达路由处理函数之前和响应返回客户端之后执行的代码。它可以：
    - 处理请求前的预处理（如认证、日志）
    - 处理响应后的后处理（如添加响应头）
    - 修改请求和响应

中间件执行流程：
    请求 → 中间件1 → 中间件2 → ... → 路由处理函数 → ... → 中间件2 → 中间件1 → 响应

中间件使用：1.定义中间件 2.注册中间件

中间件定义的几种方式：
    - 装饰器方式（函数式）：最常用、最简洁的方式，适合快速开发。
    - 继承 BaseHTTPMiddleware（类式，推荐自定义复杂中间件）：Starlette 提供的便捷基类，封装了 ASGI 细节。
    - 直接实现 ASGI 规范（底层方式）：最高性能、最大控制力，直接操作 scope, receive, send。一般使用的封装好的中间件是基于 ASGI 规范实现的。

"""
# ========== 配置常量 ==========
API_PATHS: Final[tuple[str, ...]] = (
    "/api02_basic",
    "/api02_middleware"
)

EXCLUDED_PATHS: Final[tuple[str, ...]] = (
    "/api02_middleware/health",
    "/api02_middleware/metrics",
    "/api02_middleware/public"
)


# ========== 私有函数：检查路径是否匹配 ==========
def _is_path_matched(request: Request) -> bool:
    """
    检查请求路径是否匹配需要中间件处理的路径
    :param request: 接口请求对象
    :return: 如果路径匹配返回 True，否则返回 False
    """
    path = request.url.path
    # 1. 先检查是否在排除列表中（优先级最高）
    if any(path.startswith(excluded) for excluded in EXCLUDED_PATHS):
        return False

    # 2. 再检查是否在包含列表中
    if any(path.startswith(prefix) for prefix in API_PATHS):
        return True

    return False


def middleware_func(app: FastAPI):
    """
    自定义中间件函数：使用 FastAPI 装饰器实现自定义中间件
    :param app:
    :return:
    """
    @app.middleware("http")
    async def middleware_func01(request: Request, call_next: RequestResponseEndpoint) -> Response:
        # 使用私有函数判断路径是否匹配
        if not _is_path_matched(request):
            return await call_next(request)

        logger.info("基于【func】 装饰器的函数中间件示例 01 请求处理前执行")
        # 等待路由处理函数执行完成
        response = await call_next(request)
        logger.info("基于【func】 装饰器的函数中间件示例 01 响应处理后执行")
        return response

    @app.middleware("http")
    async def middleware_func02(request: Request, call_next: RequestResponseEndpoint) -> Response:
        # 使用私有函数判断路径是否匹配
        if not _is_path_matched(request):
            return await call_next(request)
        logger.info("基于 【func】 装饰器的函数中间件示例 02 请求处理前执行")
        # 等待路由处理函数执行完成
        response = await call_next(request)
        logger.info("基于 【func】 装饰器的函数中间件示例 02 响应处理后执行")
        return response

class MiddlewareClass01(BaseHTTPMiddleware):
    """
    自定义中间件类：继承 BaseHTTPMiddleware 实现自定义中间件
    """
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # 使用私有函数判断路径是否匹配
        if not _is_path_matched(request):
            return await call_next(request)
        logger.info("基于【class】 类的中间件示例 01 请求处理前执行")
        # 等待路由处理函数执行完成
        response = await call_next(request)
        logger.info("基于【class】 类的中间件示例 01 响应处理后执行")
        return response

class MiddlewareClass02(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # 使用私有函数判断路径是否匹配
        if not _is_path_matched(request):
            return await call_next(request)
        logger.info("基于【class】 类的中间件示例 02 请求处理前执行")
        # 等待路由处理函数执行完成
        response = await call_next(request)
        logger.info("基于【class】 类的中间件示例 02 响应处理后执行")
        return response