"""分页游标：契约里的 ``cursor`` / ``next_cursor`` 是**不透明字符串**，调用方不得自行拼接。

本侧实现为「base64url(JSON{offset})」，与 03.2 的 ``seq`` 游标分开：
会话列表与会话历史都是「追加写、极少改序」的集合，偏移量足够稳，也不需要在
``(created_time, id)`` 上做复合 keyset 比较；翻页期间新增消息只会导致新数据落在
下一页之前，不会丢数据或重复（消息按 ``id`` 升序追加）。

编码用 urlsafe + 去掉 ``=`` 填充：游标会出现在 JSON 正文里，去掉填充可避免
调用方做 URL 编码时把它转义成 ``%3D``。
"""

import base64
import json

#: 游标的唯一合法输入错误：上层统一转成 400 ``INVALID_REQUEST``
CURSOR_INVALID = "cursor 无效：请原样回传上一页返回的 next_cursor。"


def encode_cursor(offset: int) -> str:
    """把偏移量包成不透明游标（负数按 0 处理，避免发出永远为空游标）。"""
    raw = json.dumps({"offset": max(0, int(offset))}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str | None) -> int:
    """解析游标为偏移量；``None`` / 空串表示第一页。

    异常:
    - ValueError: 游标不是本侧签发的格式（调用方拼了自造游标）。
    """
    if cursor is None or not str(cursor).strip():
        return 0
    text = str(cursor).strip()
    try:
        payload = json.loads(base64.urlsafe_b64decode(text + "=" * (-len(text) % 4)))
        offset = int(payload["offset"])
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError(CURSOR_INVALID) from exc
    if offset < 0:
        raise ValueError(CURSOR_INVALID)
    return offset
