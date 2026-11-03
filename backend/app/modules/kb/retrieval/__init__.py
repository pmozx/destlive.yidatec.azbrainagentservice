"""知识检索子模块（#07）：本目录**不对应任何表**，只承载检索的领域结构、SQL 与服务。

`crud.py` 负责「授权 + 生效指针 + 命中条件」一次下推（关键词 ILIKE / 向量 cosine 两条通道）；
`service.py` 负责打分、RRF 融合、精排、摘要与授权前置；契约字段投影（Pydantic / 错误码）在
`app/modules_api/contract/knowledge`。

管理端：`controller.py` + `admin_service.py` 提供 `/kb/retrieval` 只读检索调试台（通道配置视图 +
关键词/自然语言检索），`schema.py` §二是其 Pydantic 请求/响应模型。
"""
