"""Pydantic 自定义类型与通用字段校验。

``DateTimeStr`` 供各域 schema 复用；``ensure_non_blank`` 是「非空且非纯空白」的统一口径，
接入层（``/v1``）与业务域的 ``field_validator`` 都直接引用，避免每个模块各写一份。
"""

from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, PlainSerializer, WithJsonSchema

from app.common.constant import DATETIME_DISPLAY_FMT
from app.common.enums import RET
from app.core.exceptions import CustomException
from app.utils.time_util import format_datetime, parse_local_datetime


def ensure_non_blank(value: str) -> str:
    """校验字符串非空且至少含一个非空白字符，返回原值供 ``field_validator`` 直接使用。"""
    if not value or not value.strip():
        raise ValueError("不能为空或仅包含空白字符")
    return value


def datetime_validator(value: str | datetime) -> datetime:
    """日期时间格式验证器：接受 ``%Y-%m-%d %H:%M:%S`` 字符串或 ``datetime``。

    字符串按**业务时区**解释（与输出同口径），否则查询条件会比库内时刻早 8 小时。

    异常:
    - CustomException: 格式无效（默认 400，不再混进 5xx）。
    """
    try:
        if isinstance(value, str):
            return parse_local_datetime(value, DATETIME_DISPLAY_FMT)
        if isinstance(value, datetime):
            return value
    except Exception:
        raise CustomException(code=RET.ERROR.code, msg="无效的日期格式") from None
    raise CustomException(code=RET.ERROR.code, msg="无效的日期格式")


# 自定义日期时间字符串类型
DateTimeStr = Annotated[
    datetime,
    AfterValidator(lambda x: datetime_validator(x)),
    PlainSerializer(
        lambda x: format_datetime(x, DATETIME_DISPLAY_FMT) if isinstance(x, datetime) else str(x),
        return_type=str,
        when_used="json",
    ),
    WithJsonSchema({"type": "string"}, mode="serialization"),
]
