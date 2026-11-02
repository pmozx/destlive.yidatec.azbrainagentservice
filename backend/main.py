import sys

import uvicorn

from app.config.setting import settings


def _loop_option() -> str:
    """给 uvicorn 的 ``loop`` 参数选值（字符串，按 ``模块:属性`` 导入工厂）。

    psycopg v3 的异步模式（LangGraph 断点 ``AsyncPostgresSaver``）不支持 Windows 默认的
    ProactorEventLoop（"Psycopg cannot use the 'ProactorEventLoop' to run in async mode"），
    需要 Selector 循环；uvicorn 0.36 起把循环创建收敛成工厂参数
    （``Config.get_loop_factory``），所以这里直接指定 ``app.core.loops:loop_factory``，
    不再使用自 3.14 弃用、3.16 移除的 ``asyncio.set_event_loop_policy``。
    其它平台沿用 uvicorn 的 ``auto``（有 uvloop 就用 uvloop）。
    """
    return "app.core.loops:loop_factory" if sys.platform == "win32" else "auto"


if __name__ == "__main__":
    """
    项目启动命令（工作目录为后端根目录 backend/）：
     - 方式一：直接运行 main 文件：python main.py（Windows 下自带 Selector 循环）
     - 方式二：使用 uvicorn 启动：uvicorn app:create_app --host <HOST> --port <PORT> --reload
       端口取 env/.env.<环境> 的 PORT（dev=8002、prod=8000），不要在本文件写死；
       Windows 下需显式带上 --loop app.core.loops:loop_factory，否则默认 Proactor 会让
       LangGraph 断点降级（见 app/core/rag/checkpointer.py 的 _loop_blocker）
    """
    uvicorn.run(
        "app:create_app",
        host=settings.HOST,
        port=settings.PORT,
        # reload=settings.RELOAD,
        factory=True,
        loop=_loop_option(),
        # log_config=None,
    )