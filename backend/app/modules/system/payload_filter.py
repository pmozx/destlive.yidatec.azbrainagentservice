"""审计报文（JSONB）关键字过滤：日志审计与调用明细共用一段口径。

`request_payload` / `response_json` 是 ``JSONB`` 列，``LIKE`` 不能直接作用在 jsonb 上
（PostgreSQL 报 ``operator does not exist: jsonb ~~ unknown``），必须先 ``cast`` 成 text。
两处页面（F1 审计行、F6 调用明细）回答的是同一个问题——「哪个请求里出现过这段内容」，
因此 cast、大小写、空串处理都收在这里，避免各写一份后口径漂移。

代价要清楚：``cast(col as text) ILIKE '%kw%'`` 用不上任何索引，是整表扫描；
页面上的关键字查询应尽量与时间范围 / 路径 / 状态等条件同用。
"""

from typing import Any

from sqlalchemy import Text, cast
from sqlalchemy.sql.elements import ColumnElement

#: 关键字长度上限：更长的关键字对 ILIKE 没有收益，只会放大扫描成本（schema 层同步校验）
PAYLOAD_KEYWORD_MAX_LENGTH = 100


def pop_like_keyword(data: dict[str, Any], key: str) -> str | None:
    """从 ``search_to_dict`` 结果里取走报文关键字，空值统一按「不筛选」返回 ``None``。

    ``search_to_dict`` 会把空串也留在字典里（``("like", "")``），直接下传就变成
    ``ILIKE '%%'``——命中全表却看起来"没加条件"，所以这里必须剔除并 strip。
    """
    raw = data.pop(key, None)
    value = raw[1] if isinstance(raw, tuple) else raw
    if value is None:
        return None
    keyword = str(value).strip()
    return keyword or None


def jsonb_text_like(column: ColumnElement, keyword: str) -> ColumnElement:
    """JSONB 列按文本模糊匹配：字段名与值都能命中（匹配的是序列化后的文本）。

    ``ilike`` 让英文大小写不敏感，中文按字面匹配；该次调用没有报文（列为 ``NULL``）时
    表达式为 ``NULL``，不会被任何关键字命中——语义正好也是"没有这玩意"。
    """
    return cast(column, Text).ilike(f"%{keyword}%")
