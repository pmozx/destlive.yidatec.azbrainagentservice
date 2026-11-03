"""知识检索子模块（#07）：本目录**不对应任何表**，只承载检索的领域结构、SQL 与服务。

`crud.py` 负责「授权 + 生效指针 + 文本命中」一次下推；`service.py` 负责打分、摘要与
授权前置；契约字段投影（Pydantic / 错误码）在 `app/modules_api/contract/knowledge`。
"""
