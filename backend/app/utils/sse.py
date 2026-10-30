"""SSE（Server-Sent Events）帧工具：响应头常量与事件字典序列化。

链路层只产出 ``{"event": str, "data": dict}``，序列化在此收口；
改帧格式不必改动检索/生成逻辑。
"""

import json

# SSE 响应头：禁用 Nginx 缓冲，否则全部帧会攒到流结束才一次性吐出
SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}
SSE_MEDIA_TYPE = "text/event-stream"


def format_sse(event: dict) -> str:
    """事件字典 → SSE 帧（``event:`` + 单行 ``data:`` + 空行结尾）。"""
    payload = json.dumps(event["data"], ensure_ascii=False, separators=(",", ":"))
    return f"event: {event['event']}\ndata: {payload}\n\n"
