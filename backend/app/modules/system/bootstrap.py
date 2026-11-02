"""system 域启动引导：表结构与元数据（一表一目录，DDL 统一收敛在此）。

导入模型即把 6 张表注册进 ``MappedBase.metadata``（``models.py`` 覆盖 5 张台账，审计表
单独导入），启动期建表与测试都依赖这一点。建表口径见 ``app.core.table_bootstrap``。
"""

from sqlalchemy.sql.schema import Table

from app.config.setting import settings
from app.core.table_bootstrap import create_missing_tables

from .api_log.model import SystemApiLogModel
from .models import (
    SystemAuthCacheModel,
    SystemIdempotencyModel,
    SystemNonceModel,
    SystemPromptTemplateModel,
    SystemRequestLogModel,
)

# 审计表受 API_LOG_AUTO_CREATE 开关控制（关闭表示由运维手工建表）；其余 5 张技术台账无条件建
TABLES: tuple[Table, ...] = (
    *((SystemApiLogModel.__table__,) if settings.API_LOG_AUTO_CREATE else ()),
    SystemAuthCacheModel.__table__,
    SystemIdempotencyModel.__table__,
    SystemNonceModel.__table__,
    SystemPromptTemplateModel.__table__,
    SystemRequestLogModel.__table__,
)


async def ensure_tables() -> list[str]:
    """幂等创建系统域表（审计日志 / 鉴权缓存 / 幂等 / nonce / 提示词 / 请求日志）。"""
    return await create_missing_tables(TABLES, "system")
