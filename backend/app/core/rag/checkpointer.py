"""LangGraph 断点（``AsyncPostgresSaver``）：把图执行的每个节点状态落到 PostgreSQL。

约定：
1. 断点存的是「编排状态」（走到了哪个节点、节点产出了什么），不替代业务表，
  也不替代文档流水线的续跑快照（正文/分片仍落对象存储，二者互不依赖）；
2. 一个进程只开一条长连接（``from_conn_string`` 持有连接直到关停），
   写入由 LangGraph 自己按 thread_id 管理，业务侧只用 ``get_checkpointer()``；
3. 能力缺失（非 PostgreSQL、CHECKPOINT_ENABLE=false、连不上库）一律降级为 None：
   文档流水线照常跑，只是没有节点级快照，启动不被拖住。
"""

import asyncio
import sys
from contextlib import AsyncExitStack

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.config.setting import settings
from app.core.logger import logger

_checkpointer: AsyncPostgresSaver | None = None
_stack: AsyncExitStack | None = None


def _loop_blocker() -> str | None:
    """psycopg 异步模式的运行环境检查，返回阻断原因（None 表示可用）。

    psycopg v3 不支持 Windows 默认的 ProactorEventLoop；``main.py`` 已通过 uvicorn 的 ``loop``
    工厂切到 Selector，这里再兜一层：走其它入口启动时明确告警并关闭断点，而不是让流水线在第一个节点就崩。
    """
    if sys.platform != "win32":
        return None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    if isinstance(loop, asyncio.ProactorEventLoop):
        return "Windows 默认 ProactorEventLoop 不支持 psycopg 异步，请用 python main.py 启动（Selector 循环）"
    return None


def get_checkpointer() -> AsyncPostgresSaver | None:
    """取已初始化的断点保存器；未启用/初始化失败时返回 None。"""
    return _checkpointer


async def init_checkpointer() -> None:
    """幂等初始化断点保存器（建表由 ``setup()`` 完成）。"""
    global _checkpointer, _stack
    if _checkpointer is not None:
        return
    if not settings.CHECKPOINT_ENABLE:
        logger.info("==== LangGraph 断点未启用（CHECKPOINT_ENABLE=false）====")
        return
    if not settings.psycopg_url:
        logger.warning("==== LangGraph 断点跳过：DATABASE_TYPE={} 不支持 PostgresSaver ====", settings.DATABASE_TYPE)
        return
    blocker = _loop_blocker()
    if blocker is not None:
        logger.warning("==== LangGraph 断点跳过：{} ====", blocker)
        return

    stack = AsyncExitStack()
    try:
        saver = await stack.enter_async_context(AsyncPostgresSaver.from_conn_string(settings.psycopg_url))
        await saver.setup()
    except Exception as exc:  # noqa: BLE001 断点是可观测能力，失败不阻断启动
        await stack.aclose()
        logger.error("❌ LangGraph 断点初始化失败（流水线照常执行，仅无节点级快照）: {}", exc)
        return

    _stack = stack
    _checkpointer = saver
    logger.info("==== LangGraph 断点就绪（PostgresSaver）====")


async def close_checkpointer() -> None:
    """应用关停时释放断点连接。"""
    global _checkpointer, _stack
    if _stack is not None:
        await _stack.aclose()
    _stack = None
    _checkpointer = None
