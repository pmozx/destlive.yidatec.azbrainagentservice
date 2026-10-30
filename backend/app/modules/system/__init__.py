"""系统管理域：系统级通用能力，表前缀 ``sbp_ai_system_``。

- ``api_log``：接口审计日志（``sbp_ai_system_api_log``，T1.4 扩展安全头校验四列）
- ``nonce`` / ``idempotency`` / ``request_log`` / ``prompt_template`` / ``auth_cache``：
  一表一目录，各自 ``model.py`` + ``crud.py``（nonce、idempotency 另含 ``service.py``）
- ``access_trace``：契约调用只读追踪视图
- ``models``：模型聚合入口，供 Alembic / 测试统一引用

导入 ``models`` 即把 5 张表注册进 ``MappedBase.metadata``：Alembic 基线与测试都依赖这一点，
少了这行，迁移会"看不到表"却没有任何报错。
"""

from . import models  # noqa: F401  # 导入即注册（勿删）
