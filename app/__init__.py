import importlib
import pkgutil
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, APIRouter
from yao_api import YaoAPI

from app.config.path_conf import STATIC_DIR
from app.core.logger import logger


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    应用生命周期：启动时准备操作日志审计（建表 + 过期清理），关闭时释放数据库连接。

    审计属辅助能力，初始化失败只告警不阻断启动。
    """
    from app.api.v1.module_system.log.service import cleanup_expired_operation_logs, ensure_operation_log_table

    try:
        await ensure_operation_log_table()
        purged = await cleanup_expired_operation_logs()
        logger.info("==== 操作日志审计就绪（启动清理过期记录 {} 条）====", purged)
    except Exception as e:
        logger.error(f"❌ 操作日志审计初始化失败（审计功能不可用，业务不受影响）: {e!s}")

    # 向量存储（pgvector）：建扩展、切块表与 HNSW 索引，幂等；失败只告警，
    # 由 /system/vector/status 暴露原因，避免数据库暂不可用时拖住整个应用启动
    from app.core.vector_store import ensure_vector_store

    try:
        await ensure_vector_store()
    except Exception as e:
        logger.error(f"❌ 向量存储初始化失败（pgvector 不可用，向量接口将返回错误）: {e!s}")

    yield

    from app.core.database import async_engine
    from app.core.router_class import flush_pending_writes

    # 先等在途审计写入结束，再释放连接池，避免关停瞬间丢日志
    flushed = await flush_pending_writes()
    if flushed:
        logger.info("==== 关停前等待 {} 条操作日志写入完成 ====", flushed)
    await async_engine.dispose()



def _auto_discover_routers(package_path: str) -> APIRouter:
    """
    自动扫描包下所有模块，收集所有 APIRouter 实例并注册到根路由器。
    支持任意变量名：router、user_router、admin_router 等。

    Args:
        package_path: 包的导入路径，如 "app.api"

    Returns:
        包含所有子路由的根路由器

    Example:
        app.include_router(auto_discover_routers("app.api"))
    """
    root_router = APIRouter()
    package = importlib.import_module(package_path)

    for _, module_name, is_pkg in pkgutil.walk_packages(
            package.__path__,
            f"{package.__name__}."
    ):
        if is_pkg:
            continue

        module = importlib.import_module(module_name)

        # 扫描模块中所有属性，找出所有 APIRouter 实例
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if isinstance(attr, APIRouter):
                root_router.include_router(attr)

    return root_router

def register_routers(app: FastAPI) -> None:
    """
    注册路由
    :param app: FastAPI 应用
    :return: None
    """
    from app.api.v1.module_system import system_router
    app.include_router(system_router)
    # 自动扫描并注册路由
    root_router = _auto_discover_routers("app.api")
    logger.info("自动扫描并注册路由: {}", root_router.routes)
    app.include_router(root_router)

def register_middlewares(app: FastAPI) -> None:
    """
    注册中间件
    :param app: FastAPI 应用
    :return: None
    """
    from core.middlewares import middleware_func, MiddlewareClass01, MiddlewareClass02
    # 注册装饰器中间件
    middleware_func(app)
    # 注册类中间件（注意：类中间件永远比装饰器中间件先执行）
    app.add_middleware(MiddlewareClass01)
    app.add_middleware(MiddlewareClass02)


def create_app() -> FastAPI:
    app = FastAPI(
        title="service_brain_agent",
        description="服务大脑智能体应用",
        version="1.0.0",
        lifespan=lifespan,
    )
    # register_middlewares(app)
    register_routers(app)
    logger.info('==== fastapi 项目启动中 ====')
    logger.info('==== swagger 地址：http://127.0.0.1:8002/docs ====')
    logger.info('==== redoc 地址：http://127.0.0.1:8002/redoc ====')
    logger.info('==== openapi 地址：http://127.0.0.1:8002/openapi.json ====')

    YaoAPI(
        app,
        mount_path="/docs/yao-api",
        title="service_brain_agent",
        description="服务大脑智能体应用",
        version="1.0.0",
        # group_order=["订单", "支付", "用户"],
        # doc_username="admin",  # 可选：与 doc_password 成对设置才开启登录
        # doc_password="yao123",  # 可选：与 doc_username 成对设置才开启登录
        # aggregation=[{"name": "用户服务", "url": "http://user-svc/openapi.json"}],
    )

    logger.info('==== yao-api 地址：http://127.0.0.1:8002/docs/yao-api ====')

    @app.get("/healthz", include_in_schema=False)
    async def healthz():
        """容器健康检查探针：仅验证进程与 HTTP 栈可用，不探测数据库等外部依赖"""
        return {"status": "ok"}

    # 挂载前端构建产物：frontend 执行 npm run build 后输出到 static 目录
    app.frontend("/ui", directory=STATIC_DIR, fallback="index.html")
    logger.info('==== 前端页面地址：http://127.0.0.1:8002/ui ====')

    return app