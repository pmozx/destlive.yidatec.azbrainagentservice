"""系统管理域：系统级通用能力，表前缀 ``sbp_ai_system_``。

- ``api_log``：接口审计日志（``sbp_ai_system_api_log``，T1.4 扩展安全头校验四列）
- ``nonce`` / ``idempotency`` / ``request_log`` / ``prompt_template`` / ``auth_cache``：
  一表一目录，各自 ``model.py`` + ``crud.py``（nonce、idempotency 另含 ``service.py``）
- ``access_trace``：契约调用只读追踪视图
- ``storage``：对象存储目录浏览 / 上传 / 删除视图（无数据表，直接编排 ``core.clients.s3``）
- ``models``：模型聚合入口，供启动期建表与测试统一引用

导入 ``models`` 即把 5 张表注册进 ``MappedBase.metadata``：启动期建表与测试都依赖这一点，
少了这行，建表会"看不到表"却没有任何报错。
"""

from . import models  # noqa: F401  # 导入即注册（勿删）
