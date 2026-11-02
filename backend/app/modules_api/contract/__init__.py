"""正式 `/v1` 契约接入层（P1：T1.1–T1.5），与 `/api/v1`（管理台 JWT）分离。

模块分工：

| 模块 | 职责 | 任务 |
| --- | --- | --- |
| `errors.py` | 契约错误码全量表 + 错误体路由类（继承审计路由类） | T1.2 |
| `signature.py` | 验签口径翻译：算法在 `app/core/security_signature.py`，本模块只把失败翻成大写的契约码 | T1.1 |
| `nonce.py` | nonce 原子登记（`sbp_ai_system_nonce`，跨实例共享 + TTL） | T1.1 |
| `idempotency.py` | 三类幂等台账（job / request / event）、异正文 409、墓碑 | T1.3 |
| `audit.py` | `security_check` JSONB 结构装载（签名只存摘要） | T1.4 |
| `deps.py` | 验签依赖、授权时效、幂等预约依赖、启动自检 | T1.1 / T1.3 |
| `common.py` | 统一路由构造、错误响应声明、骨架期 501 | T1.5 |
| `parse/` | #01–#02 的 controller / service / schema | T2 |
| `knowledge/` | #03–#07、#16–#20 的 controller / service / schema | T3 / T6 |
| `chat/` | #08–#15 的 controller | T5 |

路由挂载由 `app/modules_api/router.py` 的总表完成（`register_api`），本包不做 `include_router`。
验签/防重放/授权时效算法在 `app/core/security_signature.py`，参数在 `setting.py` 的 `CONTRACT_*` 组。

`ensure_contract_store()` 是启动自检入口：清理过期 nonce 与过期幂等台账。
"""

from app.config.setting import settings


async def ensure_contract_store() -> None:
    """启动自检：清理过期 nonce 与过期幂等台账（失败只告警，不影响主业务启动）。"""
    if not settings.CONTRACT_V1_ENABLE:
        return
    from app.modules_api.contract.deps import contract_store_ready

    await contract_store_ready()
