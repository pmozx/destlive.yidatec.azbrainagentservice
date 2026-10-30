"""事件循环工厂：交给 uvicorn 的 ``loop`` 参数，替代已弃用的 ``asyncio.set_event_loop_policy``。

背景：psycopg v3 的异步模式（LangGraph 断点 ``AsyncPostgresSaver``）不能在 Windows 默认的
ProactorEventLoop 上运行（"Psycopg cannot use the 'ProactorEventLoop' to run in async mode"），
必须换 Selector。旧写法是在建循环之前设全局策略
（``asyncio.set_event_loop_policy(WindowsSelectorEventLoopPolicy())``），但该 API 自
Python 3.14 起弃用、3.16 移除；uvicorn 0.36 起把循环创建收敛为 ``loop`` 工厂
（``Config.get_loop_factory`` → ``asyncio.run(..., loop_factory=...)``），
因此这里直接提供工厂函数，不碰任何策略接口，也不依赖进程级全局状态。

用法：``uvicorn.run(..., loop="app.core.loops:loop_factory")``（Windows 下由 ``main.py`` 自动带上）。
"""

import asyncio
import sys


def loop_factory() -> asyncio.AbstractEventLoop:
    """Windows 返回 Selector 循环，其余平台返回默认循环。

    非 Windows 只是「给个能用的循环」兜底：那里的 ``loop=auto`` 会优先选 uvloop，
    不需要专门指定本工厂，``main.py`` 也只在 Windows 才传它。
    """
    if sys.platform == "win32":
        return asyncio.SelectorEventLoop()
    return asyncio.new_event_loop()
