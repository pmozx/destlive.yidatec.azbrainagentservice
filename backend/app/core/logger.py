import logging
import sys
from contextvars import ContextVar, Token

from loguru import logger

from app.config.path_conf import LOG_DIR
from app.config.setting import settings

# ── 请求链路 ID（日志追踪） ──
_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="")


def set_correlation_id(cid: str) -> Token:
    return _correlation_id.set(cid)


def get_correlation_id() -> str:
    return _correlation_id.get()


def reset_correlation_id(token: Token) -> None:
    _correlation_id.reset(token)


def _context_patcher(record):
    cid = get_correlation_id()
    record["extra"]["ctx"] = f" | cid={cid[:8]}" if cid else ""


class InterceptHandler(logging.Handler):
    """将标准库 logging 重定向到 Loguru

    来源三要素直接取 LogRecord 自带的 name/funcName/lineno：标准库自身的
    findCaller 已经跳过 logging 内部帧，是唯一可信的调用点。
    用 logger.opt(depth=...) 反推栈帧不行——emit 的 frame 就是本文件，
    depth 会停在 "logging 内部帧" 上，日志里恒为 logging:callHandlers:NNNN。
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        def apply_source(entry) -> None:
            entry.update(name=record.name, function=record.funcName, line=record.lineno)

        logger.patch(apply_source).opt(exception=record.exc_info).log(level, "{}", record.getMessage())


def setup_logger() -> None:
    """配置日志记录器"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger.remove()
    logger.configure(patcher=_context_patcher)

    LOG_FMT = "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>{extra[ctx]}"
    logger.add(
        sys.stdout,
        format=LOG_FMT,
        backtrace=True,
        diagnose=True,
        catch=True,
        level=settings.LOGGER_LEVEL,
        colorize=True,
    )
    logger.add(
        sink=str(LOG_DIR / "fastapiadmin.log"),
        format=LOG_FMT,
        level=settings.LOGGER_LEVEL,
        backtrace=True,
        diagnose=True,
        catch=True,
        rotation="00:00",
        retention=30,
        compression="gz",
        encoding="utf-8",
    )

    logging.basicConfig(handlers=[InterceptHandler()], level=settings.LOGGER_LEVEL, force=True)
    for name in [k for k in logging.root.manager.loggerDict if isinstance(k, str)] + ["uvicorn", "uvicorn.error", "uvicorn.access"]:
        std = logging.getLogger(name)
        std.handlers = [InterceptHandler()]
        std.propagate = False

    # APScheduler 的 DEBUG 轮询日志干扰太大，只保留 WARNING 以上
    for name in ("apscheduler", "apscheduler.schedulers", "apscheduler.jobstores"):
        logging.getLogger(name).setLevel(logging.WARNING)

    # watchfiles 每次文件变更打一条 DEBUG（“N change detected: {...}”）：热重载的监听目录里就有
    # logs/fastapiadmin.log，不压住会形成「写日志 → 检测到变更 → 再写日志」的自激循环，
    # 控制台与日志文件都被刷屏（把 LOGGER_LEVEL 调成 INFO 也压不住，因为记录本身就是这条截获链产生的）
    for name in ("watchfiles", "watchfiles.main"):
        logging.getLogger(name).setLevel(logging.WARNING)


setup_logger()
