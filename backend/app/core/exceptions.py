from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError, ResponseValidationError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

from app.common.enums import RET, EnvironmentEnum
from app.common.response import ErrorResponse
from app.config.setting import settings
from app.core.logger import logger


class CustomException(Exception):
    """业务异常：**默认 400**（业务拒绝），不要拿它表达 5xx。

    默认值曾是 500，导致「数据不存在」这类可预期拒绝与真实故障混在一起，
    监控无法区分；需要 401/403/404/409 时显式传状态码（``get_or_404`` 已固定 404）。
    真 5xx 请交给 ``SQLAlchemyError`` / 未捕获异常的统一出口。
    """

    def __init__(
        self,
        msg: str = RET.EXCEPTION.msg,
        code: int = RET.EXCEPTION.code,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        data: Any | None = None,
        success: bool = False,
    ) -> None:
        super().__init__(msg)
        self.status_code = status_code
        self.code = code
        self.msg = msg
        self.data = data
        self.success = success

    def __str__(self) -> str:
        return self.msg


def handle_exception(app: FastAPI) -> None:
    @app.exception_handler(CustomException)
    async def custom_exception_handler(request: Request, exc: CustomException) -> JSONResponse:
        logger.error(
            "[自定义异常] {} {} | status={} | code={} | msg={} | data={}",
            request.method,
            request.url.path,
            exc.status_code,
            exc.code,
            exc.msg,
            exc.data,
        )
        # 生产环境不外泄 data（可能含 SQL 字段、约束名等内部细节）；
        # 5xx 的 msg 同样常常是拼进去的底层报错，只回固定话术
        is_prod = settings.ENVIRONMENT == EnvironmentEnum.PROD
        expose_data = None if is_prod else exc.data
        msg = exc.msg
        if is_prod and exc.status_code >= status.HTTP_500_INTERNAL_SERVER_ERROR:
            msg = "服务器内部错误"
        return ErrorResponse(msg=msg, code=exc.code, status_code=exc.status_code, data=expose_data)

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        logger.error(
            "[HTTP异常] {} {} | status_code={} | detail={}",
            request.method,
            request.url.path,
            exc.status_code,
            exc.detail,
        )
        return ErrorResponse(msg=exc.detail, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = exc.errors()
        msg = errors[0].get("msg", str(errors[0])) if errors else "请求参数验证失败"
        if msg.startswith("Value error"):
            msg = msg[11:].lstrip(" ,")
        logger.error(
            "[参数验证异常] {} {} | errors={}",
            request.method,
            request.url.path,
            errors,
        )
        return ErrorResponse(msg=str(msg), status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, data=errors)

    @app.exception_handler(ResponseValidationError)
    async def response_validation_handler(request: Request, exc: ResponseValidationError) -> JSONResponse:
        logger.error(
            "[响应验证异常] {} {} | errors={}",
            request.method,
            request.url.path,
            exc.errors(),
        )
        return ErrorResponse(msg="服务器响应格式错误", status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, data=exc.body)

    @app.exception_handler(SQLAlchemyError)
    async def sqlalchemy_exception_handler(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        exc_type = type(exc).__name__

        if isinstance(exc, IntegrityError):
            detail = str(exc.orig) if exc.orig else str(exc)
            expose_detail = detail if settings.ENVIRONMENT != EnvironmentEnum.PROD else None
            if "connect" in detail or "connection" in detail:
                # 注意：Starlette 没有 HTTP_403_SERVICE_UNAVAILABLE，此处曾写成该常量，
                # 一进这个分支就 AttributeError，最终反而变成 500
                return ErrorResponse(msg="数据库连接失败", status_code=status.HTTP_503_SERVICE_UNAVAILABLE, data=expose_detail)
            if "Duplicate entry" in detail:
                return ErrorResponse(msg="数据重复，请检查唯一字段", status_code=status.HTTP_409_CONFLICT, data=expose_detail)
            if "foreign key constraint" in detail:
                return ErrorResponse(msg="存在关联数据，无法删除", status_code=status.HTTP_409_CONFLICT, data=expose_detail)
            if "cannot be null" in detail:
                return ErrorResponse(msg="必填字段缺失", status_code=status.HTTP_409_CONFLICT, data=expose_detail)
            return ErrorResponse(msg="数据已存在或违反完整性约束", status_code=status.HTTP_409_CONFLICT, data=expose_detail)

        logger.error("[数据库异常] {} {} | type={} | detail={}", request.method, request.url.path, exc_type, exc)
        data = str(exc) if settings.ENVIRONMENT != EnvironmentEnum.PROD else None
        return ErrorResponse(msg=f"数据库操作失败: {exc_type}", status_code=status.HTTP_400_BAD_REQUEST, data=data)

    @app.exception_handler(ValueError)
    async def value_exception_handler(request: Request, exc: ValueError) -> JSONResponse:
        logger.error("[值异常] {} {} | msg={}", request.method, request.url.path, exc)
        return ErrorResponse(msg=str(exc), status_code=status.HTTP_400_BAD_REQUEST)

    @app.exception_handler(Exception)
    async def all_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        exc_type = type(exc).__name__
        logger.error(
            "[未捕获异常] {} {} | type={} | detail={}",
            request.method,
            request.url.path,
            exc_type,
            exc,
        )
        return ErrorResponse(msg="服务器内部错误", status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
