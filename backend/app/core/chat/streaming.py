"""LLM 流式输出消费（通用能力）：首字超时、空闲超时、总预算、客户端断连检测与流的兜底关闭。

口径：
1. 首片给 ``first_token_timeout``（等太久说明上游排队），之后按 ``idle_timeout`` 判定——
   长答案不该被总时长误杀；
2. ``deadline`` 是**整条链路的绝对预算**（``loop.time()`` 口径）：每片等待时长取
   ``min(空闲超时, 剩余预算)``，耗尽即抛 :class:`StreamTimeoutError`，避免「每步都在超时
   之内、整体早该放弃」；
3. 每 ``disconnect_check_interval`` 片探一次断连：客户端已经走了就没必要继续烧 token；
4. 上游异常统一折算为 :class:`StreamInterruptedError`，由上层决定是否落库半截答案
   （本模块只负责中断判定，不写任何业务表）；
5. 超时/中断异常与 LLM 客户端无关，任何实现了 ``__aiter__`` 的流都能复用。

``iter_with_heartbeat`` 是同一套思路的「等待版」：检索、落库这类没有增量但可能很慢的 await
期间按固定间隔产出心跳占位，让上层能发 ``ping`` 帧（网关空闲断连阈值常在 30–60s）。
"""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from app.core.logger import logger

T = TypeVar("T")


class StreamTimeoutError(Exception):
    """流式生成超时（首字超时、空闲超时或总预算耗尽）。"""


class StreamInterruptedError(Exception):
    """流式生成被中断（客户端断连或上游异常）。"""


@dataclass(frozen=True, slots=True)
class HeartbeatTick:
    """心跳占位：被等待的协程还没结束，上层据此发一次 ping。"""


@dataclass(frozen=True, slots=True)
class HeartbeatResult(Generic[T]):
    """被等待协程的返回值：它是 ``iter_with_heartbeat`` 最后产出的元素。"""

    value: T


#: 心跳占位单例：消费方用 ``item is TICK`` 判定，不需要读字段
TICK = HeartbeatTick()


async def iter_stream(
    stream: AsyncIterator[Any],
    *,
    first_token_timeout: float,
    idle_timeout: float,
    is_disconnected: Callable[[], Awaitable[bool]] | None = None,
    disconnect_check_interval: int = 20,
    deadline: float | None = None,
) -> AsyncIterator[Any]:
    """按超时与断连策略消费异步流，逐片透传。

    :param stream: LLM 客户端返回的异步流（元素由调用方解释，本模块不关心字段）
    :param first_token_timeout: 首片等待上限（秒）
    :param idle_timeout: 后续每片等待上限（秒）
    :param is_disconnected: 可选的断连探测（如 ``request.is_disconnected``）
    :param disconnect_check_interval: 每消费多少片探一次断连（<=0 表示不探测）
    :param deadline: 可选的绝对预算（``asyncio.get_running_loop().time()`` 口径），耗尽即抛超时
    """
    loop = asyncio.get_running_loop()
    iterator = stream.__aiter__()
    first = True
    received = 0
    try:
        while True:
            timeout = first_token_timeout if first else idle_timeout
            if deadline is not None:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise StreamTimeoutError("流式生成已耗尽总预算")
                timeout = min(timeout, remaining)
            try:
                chunk = await asyncio.wait_for(iterator.__anext__(), timeout)
            except StopAsyncIteration:
                return
            except TimeoutError as exc:
                raise StreamTimeoutError("流式生成超时（首字、空闲或总预算超过上限）") from exc
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


async def iter_with_heartbeat(
    awaitable: Awaitable[T],
    *,
    interval: float,
) -> AsyncIterator[HeartbeatTick | HeartbeatResult[T]]:
    """等待一个协程，期间每隔 ``interval`` 秒产出一个 :data:`TICK`。

    典型用法（检索这类没有增量、却可能超过网关空闲阈值的等待）::

        async for item in iter_with_heartbeat(self._retrieve(question), interval=15):
            if item is TICK:
                yield ping_frame()
            else:
                evidences = item.value

    协程抛出的异常在最后一次迭代处原样抛出；消费方提前退出时协程被取消，不会泄漏任务。
    """
    task: asyncio.Task[T] = asyncio.ensure_future(awaitable)
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=interval)
            if done:
                yield HeartbeatResult(task.result())
                return
            yield TICK
    finally:
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task


async def close_stream(stream: Any) -> None:
    """尽力关闭流（超时被取消时生成器可能已在运行中，异常忽略）。"""
    aclose = getattr(stream, "aclose", None)
    if aclose is None:
        return
    with suppress(Exception):
        await aclose()
