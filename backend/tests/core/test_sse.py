"""SSE 帧序列化与解析（`app.utils.sse` + `tests.support.parse_sse_frames`）。

口径来源：`00-document/03-服务大脑AI项目开发规范文档/02-AI对话SSE流式输出规范.md` §三（S7–S10）。
这里只验**帧形本身**（编排层的帧序在 `tests/modules/kb/test_chat_stream.py`）：

1. 帧固定为 ``event:`` + 单行 ``data:`` + 空行，正文里的换行被 JSON 转义，不会撕开帧边界；
2. 中文不转义（抓包可读、字节更少）；
3. 响应头必须能穿透代理（缺 ``X-Accel-Buffering: no`` 会被攒成一整包）；
4. 序列化与解析互为逆运算——后端写帧与客户端解帧是同一份口径（S37）。
"""

import json

from app.utils.sse import SSE_HEADERS, SSE_MEDIA_TYPE, format_sse

from tests.support import parse_sse_frames


def test_format_sse_writes_event_and_single_data_line() -> None:
    """一帧两条有效行 + 空行结尾：客户端按 ``\\n\\n`` 切块才能稳定切准。"""
    frame = format_sse({"event": "delta", "data": {"seq": 1, "text": "你好"}})

    assert frame.endswith("\n\n")
    lines = frame.rstrip("\n").split("\n")
    assert lines == ["event: delta", 'data: {"seq":1,"text":"你好"}']


def test_format_sse_escapes_embedded_newlines_instead_of_splitting_frame() -> None:
    """正文含换行时仍是单行 ``data:``（JSON 转义 ``\\n``），帧内只有一个结束空行。"""
    frame = format_sse({"event": "delta", "data": {"text": "第一行\n第二行"}})

    assert frame.count("\n\n") == 1
    assert len(frame.rstrip("\n").split("\n")) == 2
    # 反序列化后换行原样还在：转义只发生在传输层
    assert json.loads(frame.rstrip("\n").split("\n")[1].removeprefix("data: "))["text"] == "第一行\n第二行"


def test_sse_headers_disable_buffering_and_transform() -> None:
    """这两个头少一个，线上就会「一次性吐出整段」或分块被再压缩（S6 / S38）。"""
    assert SSE_MEDIA_TYPE == "text/event-stream"
    assert SSE_HEADERS["X-Accel-Buffering"] == "no"
    assert "no-transform" in SSE_HEADERS["Cache-Control"]


def test_format_and_parse_roundtrip() -> None:
    """写帧 → 解帧必须还原成同一批事件；注释行（``:``）按规范忽略。"""
    events = [
        {"event": "meta", "data": {"v": 1, "request_id": "req-1", "session_id": "sess-1"}},
        {"event": "stage", "data": {"phase": "retrieving", "elapsed_ms": 3}},
        {"event": "delta", "data": {"seq": 1, "text": "按 [1] 在服务台提交申请。"}},
        {"event": "citations", "data": {"citations": []}},
        {"event": "done", "data": {"answer": "按 [1] 在服务台提交申请。", "no_evidence": False}},
    ]
    text = ": keep-alive\n\n" + "".join(format_sse(event) for event in events)

    assert parse_sse_frames(text) == events


def test_parse_sse_accepts_crlf_from_proxies() -> None:
    """中间层可能把换行改写成 CRLF：解析器不能因此切不出帧。"""
    text = "event: ping\r\ndata: {\"elapsed_ms\": 15}\r\n\r\n"

    assert parse_sse_frames(text) == [{"event": "ping", "data": {"elapsed_ms": 15}}]


def test_parse_sse_flushes_last_frame_without_trailing_blank_line() -> None:
    """连接被掐断时最后一帧常常缺空行：能解析就解析，不要静默丢掉。"""
    text = 'event: error\ndata: {"code": "TIMEOUT", "message": "超时", "retryable": true}'

    assert parse_sse_frames(text) == [
        {"event": "error", "data": {"code": "TIMEOUT", "message": "超时", "retryable": True}}
    ]
