from datetime import date, datetime, time
from typing import Any

from fastapi import status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.common.constant import DATE_DISPLAY_FMT, DATETIME_DISPLAY_FMT, TIME_DISPLAY_FMT
from app.common.enums import RET
from app.utils.time_util import format_datetime

# 裸 datetime/date/time（未走 Pydantic 的 dict 等）JSON 输出与 constant 中展示格式一致：
# datetime 必须换算到业务时区，否则把 UTC 墙上时间当本地时间展示会差 8 小时
_JSON_DATETIME_CUSTOM_ENCODER: dict[type[Any], Any] = {
    datetime: lambda d: format_datetime(d, DATETIME_DISPLAY_FMT),
    date: lambda d: d.strftime(DATE_DISPLAY_FMT),
    time: lambda t: t.strftime(TIME_DISPLAY_FMT),
}


def jsonable_response_content(content: Any) -> Any:
    return jsonable_encoder(content, custom_encoder=_JSON_DATETIME_CUSTOM_ENCODER)


class ResponseSchema[T](BaseModel):
    """响应模型"""

    code: int = Field(default=RET.OK.code, description="业务状态码")
    msg: str = Field(default=RET.OK.msg, description="响应消息")
    data: T | None = Field(default=None, description="响应数据")
    status_code: int = Field(default=status.HTTP_200_OK, description="HTTP状态码")
    success: bool = Field(default=True, description="操作是否成功")


class SuccessResponse(JSONResponse):
    """成功响应类"""

    def __init__(
        self,
        data: Any | None = None,
        msg: str = RET.OK.msg,
        code: int = RET.OK.code,
        status_code: int = status.HTTP_200_OK,
        success: bool = True,
    ) -> None:
        """初始化成功响应类

        参数:
        - data (Any | None): 响应数据。
        - msg (str): 响应消息。
        - code (int): 业务状态码。
        - status_code (int): HTTP 状态码。
        - success (bool): 操作是否成功。

        返回:
        - None
        """
        content = ResponseSchema(
            code=code,
            msg=msg,
            data=data,
            status_code=status_code,
            success=success,
        ).model_dump()
        super().__init__(content=jsonable_response_content(content), status_code=status_code)
        self.headers["Content-Type"] = "application/json; charset=utf-8"


class ErrorResponse(JSONResponse):
    """错误响应类"""

    def __init__(
        self,
        data: Any = None,
        msg: str = RET.ERROR.msg,
        code: int = RET.ERROR.code,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        success: bool = False,
    ) -> None:
        """初始化错误响应类

        参数:
        - data (Any): 响应数据。
        - msg (str): 响应消息。
        - code (int): 业务状态码。
        - status_code (int): HTTP 状态码。
        - success (bool): 操作是否成功。

        返回:
        - None
        """
        content = ResponseSchema(
            code=code,
            msg=msg,
            data=data,
            status_code=status_code,
            success=success,
        ).model_dump()
        super().__init__(content=jsonable_response_content(content), status_code=status_code)
        self.headers["Content-Type"] = "application/json; charset=utf-8"
