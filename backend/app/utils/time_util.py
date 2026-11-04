"""时间展示口径：库内 ``timestamptz`` 按 UTC 存，接口统一换算到业务时区再格式化。

页面上的 ``"YYYY-MM-DD HH:MM:SS"`` 没有时区后缀，浏览器会当**本地时间**解析；
如果这里把 UTC 墙上时间原样吐出去，北京时间用户看到的时间就比库里（+08:00 会话）
看到的早 8 小时——库里的值是对的，错的只是展示口径。

入参与出参同口径：查询条件里的无时区字符串按业务时区解释，才能和库里的 instant 对上。
"""

from datetime import UTC, datetime, tzinfo
from functools import lru_cache
from zoneinfo import ZoneInfo

from app.config.setting import settings


@lru_cache(maxsize=8)
def app_timezone() -> tzinfo:
    """业务时区（``TIMEZONE``，默认 ``Asia/Shanghai``）。

    填了非法值直接抛 ``ZoneInfoNotFoundError``：宁可在启动/首次输出时暴露，
    也不要静默退回 UTC 再把「差 8 小时」藏起来。
    """
    return ZoneInfo(settings.TIMEZONE)


def to_app_datetime(value: datetime) -> datetime:
    """换算到业务时区：naive 值按 UTC 解释（应用侧写入与库默认都按 UTC）。"""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(app_timezone())


def format_datetime(value: datetime, fmt: str) -> str:
    """按业务时区格式化（不带偏移量，供页面直接展示）。"""
    return to_app_datetime(value).strftime(fmt)


def parse_local_datetime(text: str, fmt: str) -> datetime:
    """把无时区字符串解析成业务时区时间（与 :func:`format_datetime` 互逆）。"""
    return datetime.strptime(text, fmt).replace(tzinfo=app_timezone())
