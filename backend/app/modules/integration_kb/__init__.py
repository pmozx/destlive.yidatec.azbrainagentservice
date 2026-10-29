"""集成-知识库域（integration_kb）。

职责：知识空间 / 分类 / 文档上传解析 / 分片向量化 / 任务台账 / 运营统计。
表前缀统一为 ``sbp_ai_integration_kb_*``，路由前缀 ``/integration-kb``。

边界与约定：
1. 本域不引入 RBAC：无 ``visibility`` / ``dept_ids`` / ``user_ids`` / ``owner_id`` /
   操作人审计字段，调用方即「有权限的管理端」；
2. 本域不使用 APScheduler：文档解析走进程内 asyncio 任务 + 信号量 + 独立会话回写，
   任务表只作台账（谁在跑、跑了多久、成功还是失败），不由调度器扫描重试；
3. 模型服务（LLM / Embedding / Rerank）来自 env，本域只做向量化，不建模型配置表；
4. 依赖方向：本域可 import ``app.core.clients`` 与 ``app.core`` 基础设施；
   AI 问答域（integration_chat）**不得** import 本域，引用预览等能力由本域单独暴露。
"""
