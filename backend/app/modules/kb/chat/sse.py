"""AI 对话 SSE 帧口径：事件名、错误码与 payload 构造函数。

口径来源：`00-document/03-服务大脑AI项目开发规范文档/02-AI对话SSE流式输出规范.md`（§四 / §五）。
三条约束直接落在本模块：

1. **事件名封闭**：只有 ``meta`` / ``stage`` / ``ping`` / ``delta`` / ``citations`` / ``done`` / ``error``
   七个，编排层只从这里取构造函数，不在 `service.py` 里手拼字典；
2. **帧是传输层的事**：本模块只产出 ``{"event": str, "data": dict}``，序列化在
   ``app/utils/sse.format_sse``，改帧格式不必动编排逻辑；
3. **错误码封闭**：``error`` 帧的 ``code`` 必须取自 ``ERR_*``；``retryable=True`` 的语义是
   「服务端确认一条消息都没写，调用方原样重试是安全的」。

``meta`` 必须是第一帧、``done``/``error`` 必须唯一且是最后一帧——顺序由
`service.completion_stream` 保证，本模块只负责字段形状。
"""

from collections.abc import Sequence
from typing import Any

#: 协议版本：不兼容变更（删/改事件或字段语义）时必须升版，并在 meta 首帧声明
PROTOCOL_VERSION = 1

# ── 事件名 ──────────────────────────────────────────────────────────────────
EVENT_META = "meta"
EVENT_STAGE = "stage"
EVENT_PING = "ping"
EVENT_DELTA = "delta"
EVENT_CITATIONS = "citations"
EVENT_DONE = "done"
EVENT_ERROR = "error"

# ── stage.phase（只允许这两个阶段）────────────────────────────────────────────
PHASE_RETRIEVING = "retrieving"
PHASE_GENERATING = "generating"

# ── 错误码（封闭枚举，与规范 §五 表格一一对应）─────────────────────────────────
ERR_INVALID_REQUEST = "INVALID_REQUEST"
ERR_SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
ERR_TURN_DISCARDED = "TURN_DISCARDED"
ERR_DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
ERR_MODEL_FAILED = "MODEL_FAILED"
ERR_TIMEOUT = "TIMEOUT"
ERR_INTERNAL = "INTERNAL"


def meta_event(*, session_id: str, request_id: str) -> dict[str, Any]:
    """首帧：协议版本 + 追踪 ID + 会话 ID（首轮自动建的会话也在此返回）。"""
    return {
        "event": EVENT_META,
        "data": {"v": PROTOCOL_VERSION, "request_id": request_id, "session_id": session_id},
    }


def stage_event(*, phase: str, elapsed_ms: int) -> dict[str, Any]:
    """阶段帧：让前端在首字到达前有进度可显示（检索 / 生成两段）。"""
    return {"event": EVENT_STAGE, "data": {"phase": phase, "elapsed_ms": elapsed_ms}}


def ping_event(*, elapsed_ms: int) -> dict[str, Any]:
    """心跳帧：只保活，不含业务字段（网关空闲断连阈值常在 30–60s）。"""
    return {"event": EVENT_PING, "data": {"elapsed_ms": elapsed_ms}}


def delta_event(*, seq: int, text: str) -> dict[str, Any]:
    """增量帧：``seq`` 从 1 连续递增，便于按序号定位丢帧。"""
    return {"event": EVENT_DELTA, "data": {"seq": seq, "text": text}}


def citations_event(*, citations: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """引用帧：元素与契约 #14 引用八元组同构，证据不足时为空数组。"""
    return {"event": EVENT_CITATIONS, "data": {"citations": list(citations)}}


def done_event(
    *,
    session_id: str,
    user_message_id: str,
    assistant_message_id: str,
    answer: str,
    no_evidence: bool,
    finish_reason: str,
    elapsed_ms: int,
) -> dict[str, Any]:
    """收尾帧：``answer`` 是**落库终稿**，客户端必须以它覆盖流式期间渲染的文本。"""
    return {
        "event": EVENT_DONE,
        "data": {
            "session_id": session_id,
            "user_message_id": user_message_id,
            "assistant_message_id": assistant_message_id,
            "answer": answer,
            "no_evidence": no_evidence,
            "finish_reason": finish_reason,
            "elapsed_ms": elapsed_ms,
        },
    }


def error_event(
    *,
    code: str,
    message: str,
    retryable: bool,
    session_id: str | None = None,
) -> dict[str, Any]:
    """错误帧：``message`` 面向使用者，堆栈与内部地址只进日志。"""
    return {
        "event": EVENT_ERROR,
        "data": {"code": code, "message": message, "retryable": retryable, "session_id": session_id},
    }
