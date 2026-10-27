from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config.path_conf import STATIC_DIR
from app.core.logger import logger


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    应用生命周期：启动时准备操作日志审计（建表 + 过期清理），关闭时释放数据库连接。

    审计属辅助能力，初始化失败只告警不阻断启动。
    """
    from app.modules.system.log.service import cleanup_expired_operation_logs, ensure_operation_log_table

    try:
        await ensure_operation_log_table()
        purged = await cleanup_expired_operation_logs()
        logger.info("==== 操作日志审计就绪（启动清理过期记录 {} 条）====", purged)
    except Exception as e:
        logger.error(f"❌ 操作日志审计初始化失败（审计功能不可用，业务不受影响）: {e!s}")

    # 向量存储（pgvector）：建扩展、切块表与 HNSW 索引，幂等；失败只告警，
    # 避免数据库暂不可用时拖住整个应用启动
    from app.core.vector_store import ensure_vector_store

    try:
        await ensure_vector_store()
    except Exception as e:
        logger.error(f"❌ 向量存储初始化失败（pgvector 不可用，后续向量能力将不可用）: {e!s}")

    yield

    from app.core.database import async_engine
    from app.core.router_class import flush_pending_writes

    # 先等在途审计写入结束，再释放连接池，避免关停瞬间丢日志
    flushed = await flush_pending_writes()
    if flushed:
        logger.info("==== 关停前等待 {} 条操作日志写入完成 ====", flushed)
    await async_engine.dispose()


def register_routers(app: FastAPI) -> None:
    """
    注册路由：业务模块统一由 app/api/v1/routers.py 的总表挂载。
    :param app: FastAPI 应用
    :return: None
    """
    from app.api.v1.routers import API_PREFIX, DOMAIN_CONTROLLERS, api_v1

    app.include_router(api_v1)
    # include_router 只记录占位对象，按总表统计才准确反映已挂载的 controller 数量
    logger.info(
        "已注册业务路由前缀 {}：域 {} 个，controller {} 个",
        API_PREFIX,
        len(DOMAIN_CONTROLLERS),
        sum(len(controllers) for controllers in DOMAIN_CONTROLLERS.values()),
    )


def create_app() -> FastAPI:
    app = FastAPI(
        title="service_brain_agent",
        description="服务大脑智能体应用",
        version="1.0.0",
        lifespan=lifespan,
    )
    register_routers(app)
    logger.info('==== fastapi 项目启动中 ====')
    logger.info('==== swagger 地址：http://127.0.0.1:8002/docs ====')
    logger.info('==== redoc 地址：http://127.0.0.1:8002/redoc ====')
    logger.info('==== openapi 地址：http://127.0.0.1:8002/openapi.json ====')

    @app.get("/healthz", include_in_schema=False)
    async def healthz():
        """容器健康检查探针：仅验证进程与 HTTP 栈可用，不探测数据库等外部依赖"""
        return {"status": "ok"}

    # 挂载前端构建产物：frontend 执行 npm run build 后输出到 backend/static 目录
    app.frontend("/ui", directory=STATIC_DIR, fallback="index.html")
    logger.info('==== 前端页面地址：http://127.0.0.1:8002/ui ====')

    return app