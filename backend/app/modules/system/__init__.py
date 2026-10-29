"""系统管理域：系统级通用能力，表前缀 ``sbp_ai_system_``。

- ``api_log``：接口审计日志（``sbp_ai_system_api_log``，T1.4 扩展安全头校验四列）
- ``models``：防重放 / 幂等 / 请求台账 / 提示词模板 / 授权缓存（T1.0 一次建模）

导入 ``models`` 即把 5 张表注册进 ``MappedBase.metadata``：Alembic 基线与测试都依赖这一点，
少了这行，迁移会"看不到表"却没有任何报错。
"""

from . import models  # noqa: F401  # 导入即注册（勿删）
