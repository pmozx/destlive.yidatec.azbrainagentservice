"""请求正文缓存（``app.core.request_body``）回归测试。

背景：``/v1`` 一次请求里验签、授权、幂等、审计四个环节都要读正文，原实现各自
``json.loads(await request.body())`` 一遍——大正文下 CPU 与内存峰值都翻倍（报告 P1-8）。
这里钉住“一次请求只反序列化一次”的行为，避免以后又被改回去。
"""

import json
from typing import Any

from starlette.requests import Request

from app.core import request_body


def _request(body: bytes, content_type: str = "application/json") -> Request:
    """构造不经过 HTTP 的 Request：receive 只投递一次正文。"""
    delivered = False

    async def receive() -> dict[str, Any]:
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/v1/knowledge/entries",
        "raw_path": b"/v1/knowledge/entries",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"content-type", content_type.encode())],
        "client": ("127.0.0.1", 54321),
        "server": ("testserver", 80),
        "state": {},
    }
    return Request(scope, receive)


async def test_get_json_body_parses_once_and_caches() -> None:
    """同一请求多次读取命中同一份解析结果（不是“值相等”，是同一对象）。"""
    request = _request(b'{"request_id": "req-1", "chunks": [1, 2, 3]}')

    first = await request_body.get_json_body(request)
    second = await request_body.get_json_body(request)

    assert first == {"request_id": "req-1", "chunks": [1, 2, 3]}
    assert second is first


async def test_get_json_body_counts_single_parse(monkeypatch) -> None:
    """把 json.loads 换成本地计数器：无论读几次，反序列化只发生一次。"""
    calls: list[bytes] = []
    real_loads = json.loads

    def counting_loads(data: Any, **kwargs: Any) -> Any:
        calls.append(data)
        return real_loads(data, **kwargs)

    monkeypatch.setattr(request_body.json, "loads", counting_loads)

    request = _request(b'{"a": 1}')
    for _ in range(4):
        await request_body.get_json_body(request)

    assert len(calls) == 1


async def test_non_json_body_returns_none_and_is_cached() -> None:
    """表单/二进制正文解析失败返回 None，并且同样缓存（不反复重试坏正文）。"""
    request = _request(b"not-json-at-all", content_type="text/plain")

    assert await request_body.get_json_body(request) is None
    assert await request_body.get_json_body(request) is None
    assert request.state.json_body is None


def test_peek_does_not_trigger_parse() -> None:
    """``peek_json_body`` 只读缓存：没解析过就返回 None，绝不主动读流。"""
    request = _request(b'{"a": 1}')

    assert request_body.peek_json_body(request) is None
    request.state.json_body = {"a": 1}
    assert request_body.peek_json_body(request) == {"a": 1}


def test_parse_json_bytes_handles_empty_and_broken() -> None:
    assert request_body.parse_json_bytes(b"") is None
    assert request_body.parse_json_bytes(b"{") is None
    assert request_body.parse_json_bytes(b'{"a": 1}') == {"a": 1}
