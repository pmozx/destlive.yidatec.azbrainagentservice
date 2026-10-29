"""阻塞任务线程池：把解析/分片、对象存储读写这类 CPU 或阻塞 IO 任务挪出事件循环。

为什么不用 ``asyncio.to_thread``：默认执行器不可控（大小、线程命名、关停时机），
解析一旦把默认执行器占满，会连带拖慢审计日志落库等同用默认执行器的任务；
这里用独立池 + 明确关停点（lifespan 退出时 shutdown）。

为什么开两个池：解析是分钟级长任务（MinerU 提交+轮询），若与对象存储上传共用，
一次大文档解析就能把 S3 请求堵在队列里；因此按“长耗时解析”与“短时网络等待”
分池，分别由 PARSE_THREAD_POOL_SIZE / IO_THREAD_POOL_SIZE 控制并发。
"""

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any, TypeVar

from app.config.setting import settings
from app.core.logger import logger

T = TypeVar("T")

_executor: ThreadPoolExecutor | None = None
_io_executor: ThreadPoolExecutor | None = None


def get_parse_executor() -> ThreadPoolExecutor:
    """惰性创建解析线程池（首次使用时按配置并发数建池）。"""
    global _executor
    if _executor is None:
        workers = max(1, settings.PARSE_THREAD_POOL_SIZE)
        _executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="sbp-parse")
        logger.info("==== 文档解析线程池就绪（max_workers={}）====", workers)
    return _executor


async def run_in_parse_pool(func: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    """在线程池里执行同步函数并等待结果；异常原样抛回调用方，不做吞异常。"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(get_parse_executor(), partial(func, *args, **kwargs))


def shutdown_parse_executor(*, wait: bool = True) -> None:
    """关停线程池。

    wait=True：等在途解析跑完（应用关停用，避免半截分片写库）；
    wait=False：取消排队任务（测试/热重载用）。
    """
    global _executor
    if _executor is None:
        return
    _executor.shutdown(wait=wait, cancel_futures=not wait)
    _executor = None
    logger.info("==== 文档解析线程池已关闭（wait={}）====", wait)


def get_io_executor() -> ThreadPoolExecutor:
    """惰性创建阻塞 IO 线程池（对象存储上传/下载等网络等待）。"""
    global _io_executor
    if _io_executor is None:
        workers = max(1, settings.IO_THREAD_POOL_SIZE)
        _io_executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="sbp-io")
        logger.info("==== 阻塞 IO 线程池就绪（max_workers={}）====", workers)
    return _io_executor


async def run_in_io_pool(func: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    """在线程池里执行同步阻塞调用（boto3 这类纯同步 SDK）；异常原样抛回。"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(get_io_executor(), partial(func, *args, **kwargs))


def shutdown_io_executor(*, wait: bool = True) -> None:
    """关停 IO 线程池；wait=True 等在途上传/下载结束，避免半截对象留在存储侧。"""
    global _io_executor
    if _io_executor is None:
        return
    _io_executor.shutdown(wait=wait, cancel_futures=not wait)
    _io_executor = None
    logger.info("==== 阻塞 IO 线程池已关闭（wait={}）====", wait)
