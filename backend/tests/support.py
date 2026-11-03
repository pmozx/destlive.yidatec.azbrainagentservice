"""测试共享装置：跨包复用的常量、请求构造与应用内调用。

**为什么单独一个模块**：用例按「被测对象」分包后，签名用例、路由用例、解析用例都要构造
签名头或进程内调用应用，这些能力以前靠 ``test_v1_contract.py`` 一个文件内的私有函数共享。
分包后必须显式收口，否则会在每个包里各写一份、逐渐漂移。

**约定**：这个模块只放**无副作用、与被测对象无关**的东西。任何与具体域相关的构造
（例如解析任务正文、知识条目正文）留在各自包的 ``conftest.py`` 或用例文件里。
"""

import json
import socket
from functools import lru_cache
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request as StarletteRequest

from app import create_app
from app.config.setting import settings

#: 签名用例的统一口径（自签自验，不依赖真实密钥）
SECRET = "unit-test-secret"
CLIENT_ID = "gsi-service"
NOW_MS = 1_700_000_000_000
PATH = "/v1/knowledge/search"
BODY = b'{"request_id":"req-001","tenant_id":"gsi-default","user_id":"42"}'


@lru_cache(maxsize=1)
def db_reachable() -> bool:
    """台账与冒烟用例依赖真实 PostgreSQL（JSONB + ON CONFLICT）：不可达就跳过，不让本地无库时红一片。"""
    try:
        with socket.create_connection((settings.DATABASE_HOST, settings.DATABASE_PORT), timeout=2):
            return True
    except OSError:
        return False


#: 打在需要真实库的用例上：``@requires_db``
requires_db = pytest.mark.skipif(not db_reachable(), reason="数据库不可达，跳过台账 / 冒烟用例")


def make_request(*, method: str = "POST", path: str = PATH, body: bytes = b"") -> StarletteRequest:
    """构造不经过 HTTP 的 ``Request``（审计组装单测用）：``receive`` 只投递一次正文。"""
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
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"content-type", b"application/json"), (b"user-agent", b"pytest")],
        "client": ("127.0.0.1", 54321),
        "server": ("testserver", 80),
        "state": {},
    }
    request = StarletteRequest(scope, receive)
    # 真实请求里验签依赖会把解析结果缓存在 state.json_body（见 app.core.request_body），
    # 这里等价预置，让 _failed 之类的同步出口能像真实请求那样回带 request_id
    try:
        request.state.json_body = json.loads(body) if body else None
    except ValueError:
        request.state.json_body = None
    return request


async def call(method: str, path: str, *, body: bytes = b"", headers: dict[str, str] | None = None):
    """在进程内调用应用（ASGITransport 不走网络，与用例同一个事件循环，连接池也能复用）。"""
    request_headers = {"Content-Type": "application/json"} if body else {}
    request_headers.update(headers or {})
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
        return await client.request(method, path, content=body, headers=request_headers)


def parse_sse_frames(text: str) -> list[dict[str, Any]]:
    """SSE 文本 → 帧列表 ``[{"event": str, "data": dict}]``。

    只认本项目规范内的帧形：``event:`` 一行 + 单行 ``data:`` + 空行结尾（规范 S7）；
    注释行（``:`` 开头）忽略；``data`` 不是 JSON 直接抛错——测试里不接受畸形帧。
    """
    frames: list[dict[str, Any]] = []
    event: str | None = None
    data: str | None = None
    for raw in text.splitlines():
        line = raw.rstrip("\r")
        if not line:
            if event is not None and data is not None:
                frames.append({"event": event, "data": json.loads(data)})
            event, data = None, None
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event = line.removeprefix("event:").strip()
        elif line.startswith("data:"):
            data = line.removeprefix("data:").strip()
    if event is not None and data is not None:
        frames.append({"event": event, "data": json.loads(data)})
    return frames


async def call_sse(method: str, path: str, *, body: bytes = b"", headers: dict[str, str] | None = None):
    """进程内调用流式接口并解析帧：返回 ``(response, frames)``。

    ``AsyncClient.request`` 会把响应体读完再返回，所以用例不需要逐帧交互；
    验状态码/响应头用 ``response``，验帧序用 ``frames``。
    """
    request_headers = {"Content-Type": "application/json"} if body else {}
    request_headers["Accept"] = "text/event-stream"
    request_headers.update(headers or {})
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
        response = await client.request(method, path, content=body, headers=request_headers)
    return response, parse_sse_frames(response.text)
