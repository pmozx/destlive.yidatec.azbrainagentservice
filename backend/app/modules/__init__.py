"""业务模块包：按「域 / 功能」两级分目录，对外仅暴露 controller 中的 Router。

命名规范（目录、表名、类名三者一一对应，便于 grep 与评审）：

1. 表名统一前缀 ``sbp_ai_``，形如 ``sbp_ai_[模块简写]_[业务名]``；
2. 模块简写即域目录名，当前三个业务域：
   - ``system``  系统管理          → 表前缀 ``sbp_ai_system_``
   - ``kb``      知识库管理        → 表前缀 ``sbp_ai_kb_``
   - ``voice``   语音质检管理      → 表前缀 ``sbp_ai_voice_``

   另有非业务域 ``integration``（待集成区）：承接尚未定稿的能力（LLM / 向量化 / 重排、
   文档解析分片流水线等），落库用 ``sbp_ai_integration_`` 前缀（当前唯一一张表是分片表
   ``sbp_ai_integration_chunk``，结构定稿后才迁回 kb 域）；
   单向依赖——integration 可引用业务域，业务域禁止 import integration；能力调通后整体迁入业务域并删除本域；
3. 功能目录名（如 ``api_log``、``chunk``）与表名的业务段一致；
4. 模型类名 = 去掉前缀的表名转 PascalCase + ``Model``，同目录的 CRUD / Service / Router / Schema
   复用同一词干，例如：

   ``sbp_ai_system_api_log`` ↔ ``modules/system/api_log/`` ↔ ``SystemApiLogModel`` /
   ``SystemApiLogCRUD`` / ``SystemApiLogService`` / ``SystemApiLogOutSchema`` / ``SystemApiLogRouter``

5. 索引名 ``ix_[表名]_[列]``、唯一约束 ``uq_[表名]_[列]``，同样带完整表名，避免多表索引重名。
"""
