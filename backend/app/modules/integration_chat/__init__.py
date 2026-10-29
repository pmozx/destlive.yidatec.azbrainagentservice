"""集成-AI 问答域：会话与消息、检索生成链路、SSE 流式接口。

边界与约束（与 ``integration_kb`` 保持同一套口径）：

1. **依赖方向单向**：chat → kb，读知识库只经 ``integration_kb.retrieval`` 这一个门面，
   本域不 import 知识库的 model / crud / service；引用预览与解析图片由知识库的
   ``/integration-kb/document/*`` 接口直接提供，问答域不重复实现原件读取；
2. **无 RBAC**：问答会话不带归属人字段，调用方即管理端；空间范围只做「启用的空间」校验，
   不做部门/角色数据权限；
3. **无 APScheduler**：问答是请求内链路，没有后台任务；
4. **模型来自 env**：LLM 走 ``settings.LLM_*``，向量化走 ``settings.EMBEDDING_*``，
   精排走 ``settings.RERANK_*``，均经 ``app.core.clients`` 的共享客户端调用；
5. **SSE 落库用独立会话**：请求级事务要等流结束才提交，断流反而回滚，
   因此 user / assistant 消息一律在流内用独立短事务写库。

表名：``sbp_ai_integration_chat_session`` / ``sbp_ai_integration_chat_message``。
"""
