"""`/v1` 契约基座（P1：T1.1–T1.5）：供所有对外接口复用的协议封装。

模块分工：

| 模块 | 职责 | 任务 |
| --- | --- | --- |
| `errors.py` | 契约错误码全量表 + 错误体路由类（继承审计路由类） | T1.2 |
| `signature.py` | 验签口径翻译：算法在 `app/core/security_signature.py`，本模块只把失败翻成大写的契约码 | T1.1 |
| `nonce.py` | nonce 原子登记（`sbp_ai_system_nonce`，跨实例共享 + TTL） | T1.1 |
| `idempotency.py` | 三类幂等台账（job / request / event）、异正文 409、墓碑 | T1.3 |
| `audit.py` | `security_check` JSONB 结构装载（签名只存摘要） | T1.4 |
| `deps.py` | 验签依赖、授权时效、幂等预约依赖、启动自检 | T1.1 / T1.3 |
| `common.py` | 统一路由构造、错误响应声明 | T1.5 |
| `schema.py` | 通用请求体（`request_id` / `tenant_id` / `user_id`） | T1.5 |
| `test_utils/` | 签名头生成辅助路由（受 `CONTRACT_TEST_ENABLE` 控制） | — |

**新增对外接口时**：在 `app/modules_api/<域>/<功能>/` 下写 controller / service / schema，
controller 用 `base.common.make_contract_router()` 构造 Router（审计 + 验签 + 错误体一次配齐），
错误码取 `base.errors`，幂等守卫取 `base.deps`，再到 `app/modules_api/router.py` 的总表登记。
业务域**不复制**协议口径——本包存在的意义就是让第二个、第三个业务域不必再想一遍验签与幂等。

路由挂载由 `app/modules_api/router.py` 的总表完成（`register_api`），本包不做 `include_router`。
验签/防重放/授权时效算法在 `app/core/security_signature.py`，参数在 `setting.py` 的 `CONTRACT_*` 组。

`ensure_contract_store()` 是启动自检入口：清理过期 nonce 与过期幂等台账。
"""

from app.config.setting import settings


async def ensure_contract_store() -> None:
    """启动自检：清理过期 nonce 与过期幂等台账（失败只告警，不影响主业务启动）。"""
    if not settings.CONTRACT_V1_ENABLE:
        return
    from app.modules_api.base.deps import contract_store_ready

    await contract_store_ready()
