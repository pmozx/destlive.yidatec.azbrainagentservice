"""知识平台子模块的对外 `/v1` 接口（原 `contract` 包改名而来）。

四条接口族：`parse/`（#01–#02）、`knowledge/`（#03–#07、#16–#20）、
`duplicate/`（重复治理）、`chat/`（#08–#15）。

协议基座（验签、幂等、nonce、错误体、审计）在 `app/modules_api/base/`；
本包只写业务编排：controller（薄，做「回放 → 调用 → 登记回执」）→ service → schema。
路由挂载由 `app/modules_api/router.py` 的总表完成，本包不做 `include_router`。
"""
