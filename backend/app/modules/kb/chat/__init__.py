"""问答编排子模块（#10 历史 / #14 生成）：本目录**不对应任何表**，只承载问答的领域结构、取证 SQL 与编排。

`prompt.py` 负责「提示词 → 模型消息体」的组装与上下文裁剪；
`crud.py` 负责按命中回查切片原文（引用要求逐字，不能拿摘要冒充）；
`service.py` 负责检索 → 组装 → 生成 → 落库的整条链路，以及历史消息的可见性判定；
`completion` 一次性返回、`completion_stream` 逐帧返回，两者共用同一套编排；
`sse.py` 是 SSE 帧口径（事件名、错误码、payload 构造），帧契约见
`00-document/03-…/02-AI对话SSE流式输出规范.md`；
`admin_service.py` + `controller.py` 是管理端「AI 对话」试问台（`/kb/chat/ask` 与
`/kb/chat/ask/stream`），只做身份与异常转换，编排仍走 `service.py`；
契约字段投影（Pydantic / 错误码）在 `app/modules_api/contract/chat`。
"""
