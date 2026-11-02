"""LLM 流式输出消费（通用能力）：首字超时、空闲超时、客户端断连检测与流的兜底关闭。

口径：
1. 首片给 ``first_token_timeout``（等太久说明上游排队），之后按 ``idle_timeout`` 判定——
   长答案不该被总时长误杀；
2. 每 ``disconnect_check_interval`` 片探一次断连：客户端已经走了就没必要继续烧 token；
3. 上游异常统一折算为 :class:`StreamInterruptedError`，由上层决定是否落库半截答案
   （本模块只负责中断判定，不写任何业务表）；
4. 超时/中断异常与 LLM 客户端无关，任何实现了 ``__aiter__`` 的流都能复用。
"""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress
from typing import Any

from app.core.logger import logger


class StreamTimeoutError(Exception):
    """流式生成超时（首字超时或空闲超时）。"""


class StreamInterruptedError(Exception):
    """流式生成被中断（客户端断连或上游异常）。"""


async def iter_stream(
    stream: AsyncIterator[Any],
    *,
    first_token_timeout: float,
    idle_timeout: float,
    is_disconnected: Callable[[], Awaitable[bool]] | None = None,
    disconnect_check_interval: int = 20,
) -> AsyncIterator[Any]:
    """按超时与断连策略消费异步流，逐片透传。

    :param stream: LLM 客户端返回的异步流（元素由调用方解释，本模块不关心字段）
    :param first_token_timeout: 首片等待上限（秒）
    :param idle_timeout: 后续每片等待上限（秒）
    :param is_disconnected: 可选的断连探测（如 ``request.is_disconnected``）
    :param disconnect_check_interval: 每消费多少片探一次断连（<=0 表示不探测）
    """
    iterator = stream.__aiter__()
    first = True
    received = 0
    try:
        while True:
            timeout = first_token_timeout if first else idle_timeout
            try:
                chunk = await asyncio.wait_for(iterator.__anext__(), timeout)
            except StopAsyncIteration:
                return
            except TimeoutError as exc:
                raise StreamTimeoutError("流式生成超时（首字或空闲等待超过上限）") from exc
            first = False
            received += 1
            if (
                is_disconnected is not None
                and disconnect_check_interval > 0
                and received % disconnect_check_interval == 0
                and await is_disconnected()
            ):
                raise StreamInterruptedError("客户端已断开，停止消费模型输出")
            yield chunk
    except (StreamTimeoutError, StreamInterruptedError):
        raise
    except Exception as exc:  # noqa: BLE001 上游异常统一为「生成中断」
        logger.error("模型流式生成失败：{}", exc)
        raise StreamInterruptedError(f"模型流式生成失败：{exc}") from exc


async def close_stream(stream: Any) -> None:
    """尽力关闭流（超时被取消时生成器可能已在运行中，异常忽略）。"""
    aclose = getattr(stream, "aclose", None)
    if aclose is None:
        return
    with suppress(Exception):
        await aclose()
