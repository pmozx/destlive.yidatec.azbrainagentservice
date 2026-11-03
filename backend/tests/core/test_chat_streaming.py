"""LLM 流式消费（`app.core.chat.streaming`）回归：三类超时、断连、心跳。

口径来源：`02-AI对话SSE流式输出规范.md` §六（S19–S22）。编排层的帧序在
`tests/modules/kb/test_chat_stream.py`，这里只验这个通用工具本身：

1. 首字超时与空闲超时分开判定，都折算为 :class:`StreamTimeoutError`；
2. 总预算按**绝对 deadline** 判定：剩余时间短于空闲上限时以剩余时间为准（S22）；
3. 断连探测命中即 :class:`StreamInterruptedError`，且不再透传这一片；
4. 上游异常统一折算为 :class:`StreamInterruptedError`，由上层决定是否落库；
5. 心跳：慢协程期间按期产 ``TICK``，结束后产出 ``HeartbeatResult``；消费方提前退出不留悬挂任务。
"""

import asyncio
from collections.abc import AsyncIterator

import pytest

from app.core.chat.streaming import (
    TICK,
    HeartbeatResult,
    StreamInterruptedError,
    StreamTimeoutError,
    iter_stream,
    iter_with_heartbeat,
)


async def _stream(
    chunks: list[str],
    *,
    delay: float = 0.0,
    error: Exception | None = None,
) -> AsyncIterator[str]:
    """可控的假上游：``delay`` 制造慢片，``error`` 制造上游中断。"""
    for chunk in chunks:
        if delay:
            await asyncio.sleep(delay)
        yield chunk
    if error is not None:
        raise error


async def test_iter_stream_passes_chunks_through() -> None:
    """没有超时也没有断连：逐片原样透传，末尾正常结束。"""
    received = [
        chunk async for chunk in iter_stream(_stream(["a", "b"]), first_token_timeout=1.0, idle_timeout=1.0)
    ]

    assert received == ["a", "b"]


async def test_iter_stream_first_token_timeout() -> None:
    """首片迟迟不来 → 超时（等太久说明上游排队，不该等到总预算耗尽）。"""
    with pytest.raises(StreamTimeoutError):
        async for _ in iter_stream(_stream(["太慢了"], delay=1.0), first_token_timeout=0.02, idle_timeout=1.0):
            pass


async def test_iter_stream_idle_timeout_between_chunks() -> None:
    """首片之后按空闲上限判定：已经发出的片照常生效，超时由上层决定怎么收尾。"""
    collected: list[str] = []

    with pytest.raises(StreamTimeoutError):
        async for chunk in iter_stream(_stream(["a", "b"], delay=0.1), first_token_timeout=1.0, idle_timeout=0.02):
            collected.append(chunk)

    assert collected == ["a"]


async def test_iter_stream_deadline_already_exhausted() -> None:
    """预算在开始前就耗尽：连一片都不消费，直接超时。"""
    loop = asyncio.get_running_loop()

    with pytest.raises(StreamTimeoutError, match="预算"):
        async for _ in iter_stream(
            _stream(["不该被读到"]),
            first_token_timeout=1.0,
            idle_timeout=1.0,
            deadline=loop.time() - 1.0,
        ):
            pass


async def test_iter_stream_deadline_shorter_than_idle_timeout() -> None:
    """预算比空闲上限更短时以预算为准：否则「每步都没超时、整体早该放弃」。"""
    loop = asyncio.get_running_loop()

    with pytest.raises(StreamTimeoutError):
        async for _ in iter_stream(
            _stream(["慢片"], delay=0.5),
            first_token_timeout=5.0,
            idle_timeout=5.0,
            deadline=loop.time() + 0.05,
        ):
            pass


async def test_iter_stream_stops_when_client_disconnected() -> None:
    """断连探测命中即中断，且这一片不透传（客户端已经收不到了）。"""

    async def always_disconnected() -> bool:
        return True

    collected: list[str] = []

    with pytest.raises(StreamInterruptedError, match="客户端已断开"):
        async for chunk in iter_stream(
            _stream(["a", "b"]),
            first_token_timeout=1.0,
            idle_timeout=1.0,
            is_disconnected=always_disconnected,
            disconnect_check_interval=1,
        ):
            collected.append(chunk)

    assert collected == []


async def test_iter_stream_wraps_upstream_error() -> None:
    """上游异常折成「生成中断」：上层据此发 ``MODEL_FAILED`` 而不是裸 500。"""
    with pytest.raises(StreamInterruptedError, match="上游炸了"):
        async for _ in iter_stream(
            _stream([], error=RuntimeError("上游炸了")),
            first_token_timeout=1.0,
            idle_timeout=1.0,
        ):
            pass


async def test_iter_with_heartbeat_ticks_until_result() -> None:
    """慢协程期间按期产心跳，最后一个元素是协程的返回值。"""

    async def slow() -> str:
        await asyncio.sleep(0.06)
        return "done"

    items = [item async for item in iter_with_heartbeat(slow(), interval=0.01)]

    assert isinstance(items[-1], HeartbeatResult)
    assert items[-1].value == "done"
    assert sum(1 for item in items if item is TICK) >= 1
    assert all(item is TICK for item in items[:-1])


async def test_iter_with_heartbeat_reraises_failure() -> None:
    """被等待的协程抛错：原样抛出，不包成心跳失败。"""

    async def boom() -> str:
        await asyncio.sleep(0.01)
        raise RuntimeError("检索炸了")

    with pytest.raises(RuntimeError, match="检索炸了"):
        async for item in iter_with_heartbeat(boom(), interval=0.01):
            assert item is TICK


async def test_iter_with_heartbeat_cancels_when_consumer_stops() -> None:
    """消费方提前退出（客户端断开）时协程被取消：不能留个后台任务继续烧 token。"""
    cancelled = asyncio.Event()

    async def slow() -> str:
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return "late"

    stream = iter_with_heartbeat(slow(), interval=0.01)
    assert await stream.__anext__() is TICK
    await stream.aclose()

    await asyncio.wait_for(cancelled.wait(), timeout=1.0)
