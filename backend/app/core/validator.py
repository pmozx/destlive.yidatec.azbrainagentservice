"""Pydantic 自定义类型：日期时间字符串（``DateTimeStr``）。

只保留实际被 schema 使用的类型。原文件另有 ``DateStr`` / ``TimeStr`` / ``Telephone`` / ``Email``
四个零引用别名，以及 ``menu_request_validator`` / ``role_permission_request_validator``
（本项目没有菜单与角色模块），已随死代码清理删除；需要时按同一写法再加。
"""

from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, PlainSerializer, WithJsonSchema

from app.common.constant import DATETIME_DISPLAY_FMT
from app.common.enums import RET
from app.core.exceptions import CustomException


def datetime_validator(value: str | datetime) -> datetime:
    """日期时间格式验证器：接受 ``%Y-%m-%d %H:%M:%S`` 字符串或 ``datetime``。

    异常:
    - CustomException: 格式无效（默认 400，不再混进 5xx）。
    """
    try:
        if isinstance(value, str):
            return datetime.strptime(value, DATETIME_DISPLAY_FMT)
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
        lambda x: x.strftime(DATETIME_DISPLAY_FMT) if isinstance(x, datetime) else str(x),
        return_type=str,
        when_used="json",
    ),
    WithJsonSchema({"type": "string"}, mode="serialization"),
]
