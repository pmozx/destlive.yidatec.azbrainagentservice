"""SSE（Server-Sent Events）帧工具：响应头常量与事件字典序列化。

链路层只产出 ``{"event": str, "data": dict}``，序列化在此收口；
改帧格式不必改动检索/生成逻辑。帧契约（事件名、顺序、错误码）由
``app.modules.kb.chat.sse`` 与 ``tests/support.parse_sse_frames`` 共同定义。
"""

import json

# SSE 响应头：禁用 Nginx 缓冲，否则全部帧会攒到流结束才一次性吐出；
# no-transform 阻止中间层改写/压缩分块（gzip 需要攒块，与逐帧推送直接冲突）
SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}
SSE_MEDIA_TYPE = "text/event-stream"


def format_sse(event: dict) -> str:
    """事件字典 → SSE 帧（``event:`` + 单行 ``data:`` + 空行结尾）。

    ``data`` 只写一行：多行 ``data:`` 虽然 SSE 规范允许（拼接时插 ``\\n``），但会让 JSON
    解析依赖客户端实现细节；中文不转义，线上抓包可读且字节更少。
    """
    payload = json.dumps(event["data"], ensure_ascii=False, separators=(",", ":"))
    return f"event: {event['event']}\ndata: {payload}\n\n"
