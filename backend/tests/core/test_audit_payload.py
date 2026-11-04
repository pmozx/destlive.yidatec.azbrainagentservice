"""审计载荷入库口径：结构化 JSON 落 JSONB，超长按字符串字段截断而不是整串截断。

整串截断会留下半截 JSON，既存不进 JSONB 列，也没法在详情页展开——这组用例锁住这个边界。
"""

import json

from app.core.router_class import _limit_json


def test_short_payload_keeps_structure():
    """不超限时原样保留结构（页面要展开的就是这个 dict）。"""
    payload = {"body": {"file_name": "a.pdf"}, "query": {"page_no": "1"}}
    assert _limit_json(payload, 2000) is payload


def test_long_text_is_truncated_but_json_stays_valid():
    """超长字段按字符串截断，结构完整且仍是合法 JSON。"""
    result = _limit_json({"body": {"content": "x" * 5000}}, 400)

    assert isinstance(result, dict)
    content = result["body"]["content"]
    assert content.endswith("字符)")
    assert "已截断, 原长 5000 字符" in content
    assert len(json.dumps(result, ensure_ascii=False)) <= 400


def test_too_many_fields_fall_back_to_preview():
    """字段多到压不下来时退化为带原始长度的预览骨架，而不是写一行超长 JSON。"""
    payload = {"items": [{"name": "x" * 40} for _ in range(50)]}
    result = _limit_json(payload, 200)

    assert result["_truncated"] is True
    assert result["_original_length"] > 200
    assert len(result["preview"]) <= 200


def test_zero_limit_disables_truncation():
    """``API_LOG_MAX_PAYLOAD <= 0`` 表示不限制（与 ``_truncate`` 口径一致）。"""
    payload = {"body": {"content": "x" * 5000}}
    assert _limit_json(payload, 0) is payload
