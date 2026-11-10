import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config.path_conf import STATIC_DIR
from app.core.exceptions import handle_exception
from app.core.logger import logger


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    应用生命周期：启动时准备 pgvector 扩展、按模块初始化表结构、接口审计过期清理、
    解析中断恢复、对象存储桶、种子数据、文档解析器；关闭时先等审计落库，再释放数据库连接。

    这些均为辅助/待集成能力，初始化失败只告警不阻断启动。
    """
    # AI 链路追踪（LangSmith）：必须在任何 LangChain 调用之前把配置写进 LANGSMITH_*——
    # langsmith 读取环境变量带缓存，晚于首次读取就改不动了（详见 core/observability/tracing.py）
    from app.core.observability import configure_tracing

    configure_tracing()

    # pgvector 扩展：kb 分片表的 vector 列与 HNSW 索引依赖它，必须先于各域建表。
    # 失败只告警——数据库暂不可用时不拖住整个应用启动
    from app.core.table_bootstrap import ensure_pgvector_extension

    await ensure_pgvector_extension()

    # pg_trgm 扩展：关键词检索（#07 keyword）的中文 GIN 索引依赖它，同样须先于补建索引；
    # 失败只告警——缺扩展时检索仍正确，只是退化为顺序扫描
    from app.core.table_bootstrap import ensure_pg_trgm_extension

    await ensure_pg_trgm_extension()

    # 表结构与元数据：按模块统一初始化（kb / system / voice），
    # 各域在自己的 bootstrap.py 里声明表并按依赖顺序建出，缺表才建；单域失败只告警
    from app.bootstrap import ensure_all_tables

    created = await ensure_all_tables()
    if created:
        logger.info("==== 表结构初始化完成（本次新建 {} 张）====", len(created))

    # 模型配置快照：对话档案在首次取档案时也会自然加载，但嵌入 / 重排序客户端是同步工厂，
    # 只能在构造期读内存快照——启动期先填一次，页面配好默认行后无需等首个业务请求。
    # 表不可用只告警：注册表内部已降级到 env，问答链路不该被配置表拖住
    from app.core.agents.registry import get_model_config_registry

    try:
        registry = get_model_config_registry()
        await registry.ensure_loaded()
        logger.info("==== 模型配置快照就绪（{} 行）====", registry.size)
    except Exception as e:
        logger.error(f"❌ 模型配置快照加载失败（将回落 env 配置）: {e!s}")

    # 关键词索引必须在分片表存在之后补建（IF NOT EXISTS 幂等；失败只降级检索性能）
    from app.core.table_bootstrap import ensure_keyword_index

    await ensure_keyword_index()

    # 接口审计日志（表已由 system 域 bootstrap 建好）：启动期只做过期清理
    from app.modules.system.api_log.service import SystemApiLogService

    try:
        purged = await SystemApiLogService.purge_expired()
        logger.info("==== 接口审计日志就绪（启动清理过期记录 {} 条）====", purged)
    except Exception as e:
        logger.error(f"❌ 接口审计日志过期清理失败（不影响业务）: {e!s}")

    # 上次进程中断留下的 queued/running 解析任务改判 failed，否则 #02 永远 running
    from app.modules_api.base.errors import PARSE_FAILED
    from app.modules.kb.parse_job.service import KbParseJobService

    try:
        interrupted = await KbParseJobService().reset_unfinished(
            error_code=PARSE_FAILED,
            error_message="进程重启导致解析中断，请重新提交。",
        )
        if interrupted:
            logger.warning("==== 已把 {} 个中断的解析任务改判为失败 ====", interrupted)
    except Exception as e:
        logger.error(f"❌ 中断解析任务恢复失败（需人工重新提交）: {e!s}")

    # 文档解析器：MinerU 接管 PDF/Office/图片（当前暂定的内容提取主路径）；
    # MINERU_ENABLE=false 时只用内置纯文本/HTML 解析器，二进制文档会明确报不支持
    from app.core.rag.mineru import install_mineru_parser

    try:
        install_mineru_parser()
    except Exception as e:
        logger.error(f"❌ MinerU 解析器安装失败（二进制文档将不可解析）: {e!s}")

    # 对象存储：启用时保证桶存在，页图预览与文档原件都依赖它；失败只告警，
    # 真正上传时 S3ClientError 会转成可读错误，不拖住启动
    from app.config.setting import settings

    if settings.S3_ENABLE and settings.S3_AUTO_CREATE:
        from app.core.clients import get_s3_client

        try:
            # boto3 建桶会走网络，放线程里跑
            await asyncio.to_thread(get_s3_client().ensure_bucket)
            logger.info("==== 对象存储就绪：桶 {} ====", settings.S3_BUCKET)
        except Exception as e:
            logger.error(f"❌ 对象存储初始化失败（页图预览不可用）: {e!s}")

    # 契约 /v1 台账（T1.1 / T1.3）：清理过期 nonce 与过期幂等记录。台账不可用只告警，
    # 真正校验时会以 503 AUTHORIZATION_UNAVAILABLE 明确拒绝，不会放行
    from app.modules_api.base import ensure_contract_store

    try:
        await ensure_contract_store()
    except Exception as e:
        logger.error(f"❌ 契约 /v1 台账清理失败（防重放与幂等仍可工作，仅历史记录未清理）: {e!s}")

    # 种子数据（sql/ 下一张表一个同名 JSON）：建表后按依赖顺序导入，表非空则整表跳过；
    # 失败只告警。当前种子表（空间/分类）随 integration_kb 一起删除，清单为空即跳过
    if settings.KB_SEED_ENABLE:
        from app.scripts.initialize import InitializeData

        try:
            await InitializeData().init_db()
        except Exception as e:
            logger.error(f"❌ 种子数据初始化失败（可在页面手工创建空间与分类）: {e!s}")

    yield

    from app.core.clients import close_clients
    from app.core.database import async_engine
    from app.core.router_class import flush_pending_api_logs

    # 先等在途审计写入结束，再释放连接池，避免关停瞬间丢日志
    flushed = await flush_pending_api_logs()
    if flushed:
        logger.info("==== 关停前等待 {} 条接口审计日志写入完成 ====", flushed)

    # 出站 HTTP 客户端（LLM / 向量化）用的是进程级单例，连接池要显式关闭
    closed = await close_clients()
    if closed:
        logger.info("==== 已关闭 {} 个出站 HTTP 客户端 ====", closed)

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
    # 契约 `/v1`（P1 正式接入层）：五头签名 + 幂等 + 审计；路由总表见 app/modules_api/router.py
    from app.modules_api import register_api

    register_api(app)
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