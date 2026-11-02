from typing import List, Tuple

from enum import Enum, unique


@unique
class EnvironmentEnum(str, Enum):
    """应用运行环境（开发 / 生产）。"""

    DEV = "dev"
    PROD = "prod"


@unique
class DatabaseTypeEnum(str, Enum):
    """数据库类型枚举

    用于统一数据库类型字符串，避免各处写死字符串导致拼写错误。
    继承 str 使其可直接与字符串比较（兼容 .env 文件加载的字符串值）。
    """

    MYSQL = "mysql"
    POSTGRES = "postgres"
    SQLITE = "sqlite"

    @property
    def async_driver(self) -> str:
        """返回对应的异步数据库驱动。"""
        return _ASYNC_DRIVER_MAP[self]

    @classmethod
    def async_driver_for(cls, value: str) -> str:
        """按配置里的字符串类型取异步驱动（env 读出来的都是字符串）。

        未知值回落 ``postgres``：与 ``database_url`` 的历史兜底口径一致，
        让配置写错时表现成「连不上库」而不是启动期 KeyError。
        """
        try:
            return cls(value).async_driver
        except ValueError:
            return cls.POSTGRES.async_driver

    @property
    def is_sqlite(self) -> bool:
        return self == DatabaseTypeEnum.SQLITE

    @property
    def is_mysql(self) -> bool:
        return self == DatabaseTypeEnum.MYSQL

    @property
    def is_postgres(self) -> bool:
        return self == DatabaseTypeEnum.POSTGRES

    @classmethod
    def values_list(cls) -> List[str]:
        """获取所有枚举值的列表"""
        return [item.value for item in cls]

    @classmethod
    def values_tuple(cls) -> Tuple[str, ...]:
        """获取所有枚举值的元组"""
        return tuple(item.value for item in cls)


# 数据库类型 → 异步驱动映射
_ASYNC_DRIVER_MAP: dict[str, str] = {
    DatabaseTypeEnum.MYSQL: "mysql+aiomysql",
    DatabaseTypeEnum.POSTGRES: "postgresql+asyncpg",
    DatabaseTypeEnum.SQLITE: "sqlite+aiosqlite",
}


# ==================== 系统返回码 ====================


class RET(Enum):
    """系统返回码枚举

    0~200: 成功状态码
    400~600: HTTP标准错误码
    4000+: 自定义业务错误码
    """

    # 成功状态码
    OK = (0, "成功")
    SUCCESS = (200, "操作成功")
    CREATED = (201, "创建成功")
    ACCEPTED = (202, "请求已接受")
    NO_CONTENT = (204, "操作成功,无返回数据")

    # HTTP标准错误码
    ERROR = (1, "请求错误")
    BAD_REQUEST = (400, "参数错误")
    UNAUTHORIZED = (401, "未授权")
    FORBIDDEN = (403, "访问受限")
    NOT_FOUND = (404, "资源不存在")
    BAD_METHOD = (405, "不支持的请求方法")
    NOT_ACCEPTABLE = (406, "不接受的请求")
    CONFLICT = (409, "资源冲突")
    GONE = (410, "资源已删除")
    PRECONDITION_FAILED = (412, "前提条件失败")
    UNSUPPORTED_MEDIA_TYPE = (415, "不支持的媒体类型")
    UNPROCESSABLE_ENTITY = (422, "无法处理的实体")
    TOO_MANY_REQUESTS = (429, "请求过于频繁")

    # 服务器错误码
    INTERNAL_SERVER_ERROR = (500, "服务器内部错误")
    NOT_IMPLEMENTED = (501, "功能未实现")
    BAD_GATEWAY = (502, "网关错误")
    SERVICE_UNAVAILABLE = (503, "服务不可用")
    GATEWAY_TIMEOUT = (504, "网关超时")
    HTTP_VERSION_NOT_SUPPORTED = (505, "HTTP版本不支持")

    # 自定义业务错误码
    EXCEPTION = (-1, "系统异常")
    DATAEXIST = (4003, "数据已存在")
    DATAERR = (4004, "数据错误")
    PARAMERR = (4103, "参数错误")
    IOERR = (4302, "IO错误")
    SERVERERR = (4500, "服务错误")
    UNKOWNERR = (4501, "未知错误")
    TIMEOUT = (4502, "请求超时")
    RATE_LIMIT_EXCEEDED = (4503, "访问频率超限")

    # Token相关错误码
    INVALID_TOKEN = (4504, "无效令牌")
    EXPIRED_TOKEN = (4505, "令牌过期")

    # 认证授权错误码
    INVALID_CREDENTIALS = (4506, "无效凭证")

    def __init__(self, code: int, msg: str) -> None:
        self._code = code
        self._msg = msg

    @property
    def code(self) -> int:
        return self._code

    @property
    def msg(self) -> str:
        return self._msg
