"""时间展示口径：库里存 UTC，接口按业务时区（默认 Asia/Shanghai）输出。

页面把无时区后缀的 ``YYYY-MM-DD HH:MM:SS`` 当本地时间解析，所以这里输出 UTC 墙上时间
就会整体早 8 小时——这组用例锁的就是这条口径，别再退回 ``strftime`` 直出。
"""

from datetime import UTC, datetime

from pydantic import TypeAdapter

from app.common.constant import DATETIME_DISPLAY_FMT
from app.core.validator import DateTimeStr
from app.utils.time_util import format_datetime, parse_local_datetime

_DATETIME_STR_ADAPTER = TypeAdapter(DateTimeStr)


def test_aware_utc_datetime_is_rendered_in_app_timezone():
    """库内 07:30Z 要显示成 15:30，而不是原样吐 UTC。"""
    assert format_datetime(datetime(2026, 11, 4, 7, 30, tzinfo=UTC), DATETIME_DISPLAY_FMT) == "2026-11-04 15:30:00"


def test_naive_datetime_is_treated_as_utc():
    """驱动在无时区列上可能给 naive 值，按 UTC 解释（应用侧写入用 now(UTC)）。"""
    assert format_datetime(datetime(2026, 11, 4, 7, 30), DATETIME_DISPLAY_FMT) == "2026-11-04 15:30:00"


def test_query_input_is_parsed_as_app_timezone():
    """入参同口径：页面传的本地时间要换算成对应的 instant，才能和 timestamptz 正确比较。"""
    assert parse_local_datetime("2026-11-04 15:30:00", DATETIME_DISPLAY_FMT) == datetime(2026, 11, 4, 7, 30, tzinfo=UTC)


def test_parse_and_format_are_inverse():
    """往返一致：解析再格式化，墙上时间不变。"""
    assert format_datetime(parse_local_datetime("2026-11-04 15:30:00", DATETIME_DISPLAY_FMT), DATETIME_DISPLAY_FMT) == (
        "2026-11-04 15:30:00"
    )


def test_datetimestr_serializes_through_app_timezone():
    """DateTimeStr 是所有页面时间的统一出口，序列化也必须换算。"""
    assert _DATETIME_STR_ADAPTER.dump_python(datetime(2026, 11, 4, 7, 30, tzinfo=UTC), mode="json") == (
        "2026-11-04 15:30:00"
    )
