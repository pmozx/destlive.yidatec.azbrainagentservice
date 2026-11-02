"""请求正文解析：**一次请求只反序列化一次**，结果缓存在 ``request.state.json_body``。

为什么要缓存：``/v1`` 一次请求里至少有四个地方要读正文——验签依赖（取 request_id/user_id）、
授权依赖（校验 authorization/scope）、幂等依赖（算 body_hash、取业务 ID）、审计采集（留痕）。
Starlette 的 ``request.body()`` 自带字节缓存（重复 await 不会重复读流），但 ``json.loads``
没有——于是同一份正文被解析 4 次，大正文（几十 MB 的分片清单）下 CPU 与内存峰值都翻倍。

约定：
1. 业务/依赖一律用 :func:`get_json_body`，不要自己 ``json.loads(await request.body())``；
2. 非 JSON 正文（表单、二进制）解析失败返回 ``None``，不抛异常——调用方按“拿不到结构化正文”处理；
3. 只缓存解析结果，不缓存失败原因：解析失败也缓存 ``None``，避免反复重试同一份坏正文。
"""

import json
from typing import Any

from fastapi import Request

_CACHE_KEY = "json_body"


def parse_json_bytes(body: bytes) -> Any:
    """把原始字节解析成 JSON，失败返回 ``None``（不区分空正文与坏正文）。"""
    if not body:
        return None
    try:
        return json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


async def get_json_body(request: Request) -> Any:
    """取本次请求的 JSON 正文（首次调用解析并缓存，后续直接命中缓存）。"""
    if hasattr(request.state, _CACHE_KEY):
        return getattr(request.state, _CACHE_KEY)
    try:
        body = await request.body()
    except Exception:  # noqa: BLE001 正文已被下游流式消费等场景拿不到原文
        payload = None
    else:
        payload = parse_json_bytes(body)
    setattr(request.state, _CACHE_KEY, payload)
    return payload


def peek_json_body(request: Request) -> Any:
    """只看缓存、不触发解析：给同步代码（如错误响应回带 request_id）用。"""
    return getattr(request.state, _CACHE_KEY, None)
