import os
from typing import Final, Optional, Dict
from urllib.parse import quote_plus

from dotenv import dotenv_values
from pathlib import Path
from pydantic_settings import BaseSettings
from pydantic import Field, computed_field
from app.config.path_conf import ENV_DIR, BASE_ENV_FILE
from app.common.enums import DatabaseTypeEnum

"""
应用配置模块
实现类似 Spring Boot profiles 的环境配置机制：
1. 优先读取操作系统环境变量 ENVIRONMENT
2. 其次读取 env/.env 文件中的 ENVIRONMENT
3. 以上均未配置时，默认使用 dev 环境
最终加载对应的 env/.env.{environment} 环境配置文件
"""
_ENVIRONMENT_KEY:Final[str] = "ENVIRONMENT"
_DEFAULT_ENVIRONMENT:Final[str] = "dev"

def _get_env_file() -> Path:
    """
    确定当前运行环境并返回对应的环境配置文件路径。

    优先级：操作系统环境变量 > env/.env 文件 > 默认 dev 环境
    :return: 环境配置文件路径
    """
    # 1. 优先从操作系统环境变量中获取
    environment = os.environ.get(_ENVIRONMENT_KEY)

    # 2. 操作系统环境变量未设置时，从 env/.env 文件中读取
    if not environment:
        base_config: Dict[str, Optional[str]] = dotenv_values(BASE_ENV_FILE)
        environment = base_config.get(_ENVIRONMENT_KEY)

    # 3. 以上均未配置时，使用默认 dev 环境
    if not environment:
        environment = _DEFAULT_ENVIRONMENT

    # 拼接环境配置文件路径：env/.env.{environment}
    env_file = ENV_DIR / f".env.{environment}"

    if not env_file.exists():
        raise FileNotFoundError(
            f"环境配置文件不存在: {env_file}\n"
            f"当前环境: ENVIRONMENT={environment}\n"
            f"请在 env/ 目录下创建 .env.{environment} 文件"
        )

    return env_file


class Settings(BaseSettings):
    """
    应用配置（类似 Spring Boot 的 application.yml）
    所有配置项从 env/.env.{environment} 文件中加载
    """
    model_config = {
        "env_file": _get_env_file(),
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
    }

    # ==================== 应用配置 ====================
    TITLE: str = Field(default="service_brain_agent", description="应用标题")
    DESCRIPTION: str = Field(default="服务大脑智能体应用", description="应用描述")
    VERSION: str = Field(default="1.0.0", description="应用版本")

    DEBUG: bool = Field(default=True, description="是否开启调试模式")

    # ==================== 服务配置 ====================
    HOST: str = Field(default="0.0.0.0", description="服务监听地址")
    PORT: int = Field(default=8000, description="服务监听端口")
    RELOAD: bool = Field(default=True, description="是否开启热重载")

    # ==================== 数据库配置 ====================
    # 下列默认值仅用于 env 文件缺项时的兜底，实际取值以 env/.env.<env> 为准；
    # 端口口径：39990 是宿主发布端口，容器内按容器名直连仍是 5432
    DATABASE_TYPE: str = Field(default=DatabaseTypeEnum.POSTGRES.value, description="数据库类型：postgres/mysql/sqlite")
    DATABASE_HOST: str = Field(default="localhost", description="数据库主机地址")
    DATABASE_PORT: int = Field(default=5432, description="数据库端口")
    DATABASE_USER: str = Field(default="postgres", description="数据库用户名")
    DATABASE_PASSWORD: str = Field(default="Yidatec@2026!", description="数据库密码")
    DATABASE_NAME: str = Field(default="service_brain", description="数据库名称")
    DATABASE_ECHO: bool = Field(default=False, description="是否输出 SQL 日志")

    # ==================== 向量存储配置（pgvector） ====================
    VECTOR_ENABLE: bool = Field(default=True, description="是否启用 pgvector 向量存储（仅 DATABASE_TYPE=postgres 生效）")
    VECTOR_DIM: int = Field(default=1024, description="向量维度，须与 embedding 模型输出一致（bge-m3 为 1024）")
    VECTOR_AUTO_CREATE: bool = Field(default=True, description="启动时自动创建 vector 扩展、切块表与 HNSW 索引")

    # 连接池配置
    ECHO_POOL: bool = Field(default=False, description="是否输出连接池日志")
    POOL_PRE_PING: bool = Field(default=True, description="连接前是否进行 ping 检测")
    FUTURE: bool = Field(default=True, description="是否使用 2.0 风格连接")
    POOL_RECYCLE: int = Field(default=3600, description="连接回收时间（秒）")
    POOL_SIZE: int = Field(default=5, description="连接池大小")
    MAX_OVERFLOW: int = Field(default=10, description="最大溢出连接数")
    POOL_TIMEOUT: int = Field(default=30, description="连接超时时间（秒）")
    POOL_USE_LIFO: bool = Field(default=False, description="是否使用 LIFO 连接策略")

    # 会话配置
    AUTOCOMMIT: bool = Field(default=False, description="是否自动提交")
    AUTOFLUSH: bool = Field(default=False, description="是否自动刷新")
    AUTOFETCH: bool | None = Field(default=None, description="是否自动获取（优先级高于 AUTOFLUSH）")
    EXPIRE_ON_COMMIT: bool = Field(default=False, description="提交后是否过期实例")

    # ==================== 日志配置 ====================
    LOGGER_LEVEL: str = Field(default="INFO", description="日志级别")

    # ==================== 操作日志审计配置 ====================
    OPERATION_LOG_ENABLE: bool = Field(default=True, description="是否开启操作日志审计")
    OPERATION_RECORD_METHOD: list[str] = Field(
        default=["POST", "PUT", "PATCH", "DELETE"],
        description="需要记录的请求方法（GET/HEAD/OPTIONS 默认不记录，避免查询与预检淹没写操作）",
    )
    OPERATION_LOG_SKIP_PATHS: list[str] = Field(
        default=["/docs", "/redoc", "/openapi.json", "/ui", "/static"],
        description="不参与审计的路径前缀：接口文档、前端产物与静态资源",
    )
    OPERATION_LOG_MAX_PAYLOAD: int = Field(default=2000, description="请求/响应体入库最大字符数，超出截断")
    OPERATION_LOG_USER_HEADER: str = Field(
        default="",
        description="回填操作人的请求头名；留空表示只认 request.state 回填，避免客户端伪造操作人",
    )
    OPERATION_LOG_AUTO_CREATE: bool = Field(default=True, description="启动时自动创建审计表（已存在则跳过）")
    OPERATION_LOG_RETENTION_DAYS: int = Field(default=90, description="审计日志保留天数，<=0 表示启动时不做过期清理")
    TRUSTED_PROXY_HOPS: int = Field(default=0, description="前置可信反向代理跳数，0 表示忽略代理头、仅取 TCP 对端地址")

    # 数据库驱动映射（数据库类型 -> 异步驱动）
    _DB_DRIVERS: dict = {
        DatabaseTypeEnum.MYSQL.value: DatabaseTypeEnum.MYSQL.async_driver,
        DatabaseTypeEnum.POSTGRES.value: DatabaseTypeEnum.POSTGRES.async_driver,
        DatabaseTypeEnum.SQLITE.value: DatabaseTypeEnum.SQLITE.async_driver,
    }

    @computed_field
    @property
    def database_url(self) -> str:
        """根据数据库类型自动拼接连接 URL

        口令按 URL 规则编码，避免密码含 @ / : / # 等字符时连接串被截断；
        charset 仅 MySQL 需要——asyncpg 遇到未知 query 参数会在建连阶段直接报错。
        """
        driver = self._DB_DRIVERS.get(self.DATABASE_TYPE, DatabaseTypeEnum.POSTGRES.async_driver)
        if self.DATABASE_TYPE == DatabaseTypeEnum.SQLITE.value:
            return f"{driver}:///{self.DATABASE_NAME}"
        credential = f"{quote_plus(self.DATABASE_USER)}:{quote_plus(self.DATABASE_PASSWORD)}"
        url = (
            f"{driver}://{credential}"
            f"@{self.DATABASE_HOST}:{self.DATABASE_PORT}/{self.DATABASE_NAME}"
        )
        if self.DATABASE_TYPE == DatabaseTypeEnum.MYSQL.value:
            return f"{url}?charset=utf8mb4"
        return url

    @computed_field
    @property
    def async_db_url(self) -> str:
        """异步数据库连接 URI（别名，兼容 database.py）"""
        return self.database_url


# 全局配置单例（导入即用）
settings = Settings()
