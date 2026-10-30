from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config.path_conf import STATIC_DIR
from app.core.exceptions import handle_exception
from app.core.logger import logger


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    应用生命周期：启动时准备接口审计日志（建表 + 过期清理）、向量存储、知识空间表与种子数据、
    文档解析器，关闭时先取消在跑的文档解析、再关停线程池并释放数据库连接。

    这些均为辅助/待集成能力，初始化失败只告警不阻断启动。
    """
    from app.modules.system.api_log.service import SystemApiLogService

    try:
        await SystemApiLogService.ensure_table()
        purged = await SystemApiLogService.purge_expired()
        logger.info("==== 接口审计日志就绪（启动清理过期记录 {} 条）====", purged)
    except Exception as e:
        logger.error(f"❌ 接口审计日志初始化失败（审计功能不可用，业务不受影响）: {e!s}")

    # 向量存储（pgvector）：建扩展、分片表与 HNSW 索引，幂等；失败只告警，
    # 避免数据库暂不可用时拖住整个应用启动
    from app.modules.integration_kb.chunk.vector_store import ensure_vector_store

    try:
        await ensure_vector_store()
    except Exception as e:
        logger.error(f"❌ 向量存储初始化失败（pgvector 不可用，后续向量能力将不可用）: {e!s}")

    # 知识库业务表（空间/分类/文档/任务）与 AI 问答表（会话/消息）：
    # 启动期幂等建表，并把上次进程中断留下的「解析中」改回待解析；失败只告警
    from app.modules.integration_chat.bootstrap import ensure_chat_tables
    from app.modules.integration_kb.bootstrap import ensure_kb_tables
    from app.modules.integration_kb.task.worker import reset_interrupted_documents

    try:
        await ensure_kb_tables()
        await reset_interrupted_documents()
    except Exception as e:
        logger.error(f"❌ 知识库表初始化失败（上传与解析将不可用）: {e!s}")

    try:
        await ensure_chat_tables()
    except Exception as e:
        logger.error(f"❌ AI 问答表初始化失败（问答历史无法保存）: {e!s}")

    # 文档解析器：MinerU 接管 PDF/Office/图片（当前暂定的内容提取主路径）；
    # MINERU_ENABLE=false 时只用内置纯文本/HTML 解析器，二进制文档会明确报不支持
    from app.modules.integration_kb.bootstrap import install_parser

    try:
        await install_parser()
    except Exception as e:
        logger.error(f"❌ MinerU 解析器安装失败（二进制文档将不可解析）: {e!s}")

    # 契约 /v1 台账（T1.1 / T1.3）：清理过期 nonce 与过期幂等记录。台账不可用只告警，
    # 真正校验时会以 503 AUTHORIZATION_UNAVAILABLE 明确拒绝，不会放行
    from app.contract import ensure_contract_store

    try:
        await ensure_contract_store()
    except Exception as e:
        logger.error(f"❌ 契约 /v1 台账清理失败（防重放与幂等仍可工作，仅历史记录未清理）: {e!s}")

    # 种子数据（sql/ 下一张表一个同名 JSON）：建表后按依赖顺序导入，表非空则整表跳过；
    # 失败只告警，空库也能在页面上手工创建空间与分类
    from app.config.setting import settings

    if settings.KB_SEED_ENABLE:
        from app.scripts.initialize import InitializeData

        try:
            await InitializeData().init_db()
        except Exception as e:
            logger.error(f"❌ 种子数据初始化失败（可在页面手工创建空间与分类）: {e!s}")

    # 对象存储（MinIO / 任意 S3 兼容服务）：按 S3_AUTO_CREATE 幂等建桶。
    # 失败只告警不阻断启动：MinIO 可能晚于应用就绪，真用到的上传/下载仍会明确报错
    if settings.S3_ENABLE and settings.S3_AUTO_CREATE:
        from app.modules.integration_kb.clients.s3 import S3StorageClient

        try:
            await S3StorageClient().ensure_bucket()
        except Exception as e:
            logger.error(f"❌ 对象存储桶初始化失败（S3 能力不可用，上传将报错）: {e!s}")

    yield

    from app.core.database import async_engine
    from app.core.router_class import flush_pending_api_logs

    # 先等在途审计写入结束，再释放连接池，避免关停瞬间丢日志
    flushed = await flush_pending_api_logs()
    if flushed:
        logger.info("==== 关停前等待 {} 条接口审计日志写入完成 ====", flushed)

    # 先取消在跑的文档解析任务（避免半截写入），再关对象存储/解析线程池
    from app.modules.integration_kb.task.worker import cancel_all

    cancelled = await cancel_all()
    if cancelled:
        logger.info("==== 关停前已取消 {} 个文档解析任务 ====", cancelled)

    # 解析/上传线程池可能仍有在途任务，先等其收尾再释放数据库连接；
    # 对象存储上传走独立 IO 池，先关它（等上传落盘）再关解析池
    from app.modules.integration_kb.runtime import shutdown_io_executor, shutdown_parse_executor

    shutdown_io_executor(wait=True)
    shutdown_parse_executor(wait=True)
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


def _access_base_url() -> str:
    """
    启动日志用的访问基址：监听地址与端口一律取配置（HOST / PORT），换端口启动时日志跟着走。
    监听 0.0.0.0 / :: 时本机打不开该地址，回显 127.0.0.1 便于直接点击。
    :return: 形如 http://127.0.0.1:8002
    """
    from app.config.setting import settings

    host = settings.HOST.strip()
    if host in {"", "0.0.0.0", "::", "[::]"}:
        host = "127.0.0.1"
    return f"http://{host}:{settings.PORT}"


def create_app() -> FastAPI:
    app = FastAPI(
        title="service_brain_agent",
        description="服务大脑智能体应用",
        version="1.0.0",
        lifespan=lifespan,
    )
    # 统一异常出口：CustomException / 参数校验 / 数据库异常都转成 ResponseSchema，
    # 前端才能拿到可读的 msg（否则一律是 500 Internal Server Error）
    handle_exception(app)
    register_routers(app)
    # 契约 `/v1`（P1 正式接入层）：四头签名 + 幂等 + 审计；POC 验证骨架已按阶段 0 下线
    from app.contract import register_contract

    register_contract(app)
    base_url = _access_base_url()
    logger.info('==== fastapi 项目启动中 ====')
    logger.info('==== swagger 地址：{}/docs ====', base_url)
    logger.info('==== redoc 地址：{}/redoc ====', base_url)
    logger.info('==== openapi 地址：{}/openapi.json ====', base_url)

    @app.get("/healthz", include_in_schema=False)
    async def healthz():
        """容器健康检查探针：仅验证进程与 HTTP 栈可用，不探测数据库等外部依赖"""
        return {"status": "ok"}

    # 挂载前端构建产物：frontend 执行 npm run build 后输出到 backend/static 目录
    app.frontend("/ui", directory=STATIC_DIR, fallback="index.html")
    logger.info('==== 前端页面地址：{}/ui ====', base_url)

    return app