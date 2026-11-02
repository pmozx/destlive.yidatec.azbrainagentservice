"""契约验收脚本的公共小工具（``mock_gsi`` / ``verify_m1`` 共用）。

两个脚本原先各自实现了一份 ``_json`` / ``SKELETON_STATUS`` / 错误码常量，
结果是同一件事改一处忘一处——``mock_gsi`` 断言 ``UNSUPPORTED_STREAM`` 时，
``errors.py`` 里根本没有这个码，用例永远不可能通过。这里统一收口：

1. ``json_dict``：把响应尽力解析成 dict（错误体、非 JSON 都不该打断验收流程）；
2. ``error_code``：取契约错误体里的 ``error.code``；
3. ``SKELETON_STATUS``：骨架期 501 的统一判定值（业务未实现 ≠ 用例失败）。

错误码常量本身仍以 ``app.contract.errors`` 为唯一事实源，脚本直接 ``from .errors import``。
"""

from typing import Any, Final

import httpx

# 骨架期约定：签名/审计/幂等已生效，业务实现未就绪的端点返回 501
SKELETON_STATUS: Final[int] = 501


def json_dict(response: httpx.Response) -> dict[str, Any]:
    """尽力把响应解析成 dict：非 JSON 返回 ``{"raw": 前 300 字}``，列表包一层 ``data``。"""
    try:
        data = response.json()
    except ValueError:
        return {"raw": response.text[:300]}
    return data if isinstance(data, dict) else {"data": data}


def error_code(body: dict[str, Any]) -> str:
    """取契约错误体里的错误码（``{"error": {"code": ...}}``），取不到返回空串。"""
    error = body.get("error") if isinstance(body, dict) else None
    return str((error or {}).get("code") or "") if isinstance(error, dict) else ""
