"""阻塞任务线程池：解析线程池（长耗时解析）与 IO 线程池（对象存储读写等网络等待）。"""

from .executor import (
    get_io_executor,
    get_parse_executor,
    run_in_io_pool,
    run_in_parse_pool,
    shutdown_io_executor,
    shutdown_parse_executor,
)

__all__ = [
    "get_io_executor",
    "get_parse_executor",
    "run_in_io_pool",
    "run_in_parse_pool",
    "shutdown_io_executor",
    "shutdown_parse_executor",
]
