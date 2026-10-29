"""正式 `/v1` 契约接入层（P1：T1.1–T1.5），与 `/api/v1`（管理台 JWT）、`app/poc`（验证骨架）三分。

模块分工：

| 模块 | 职责 | 任务 |
| --- | --- | --- |
| `errors.py` | 契约错误码全量表 + 错误体路由类（继承审计路由类） | T1.2 |
| `signature.py` | 四头 HMAC 验签、`authorization` ≤120s 时效校验（纯计算） | T1.1 |
| `nonce.py` | nonce 原子登记（`sbp_ai_system_nonce`，跨实例共享 + TTL） | T1.1 |
| `idempotency.py` | 三类幂等台账（job / request / event）、异正文 409、墓碑 | T1.3 |
| `audit.py` | `security_check` JSONB 结构装载（签名只存摘要） | T1.4 |
| `deps.py` | 验签依赖、幂等预约依赖、启动自检 | T1.1 / T1.3 |
| `router.py` | 20 条路由骨架（签名可打通，业务随 Tx 落地） | T1.5 |

与 POC 的关系：`CONTRACT_V1_ENABLE=true`（默认）时 POC 的 `/v1` 整体下线，两者**互斥**
（同一路径不能挂两份实现，且 POC 的错误码是小写、nonce 在内存里，都不是正式口径）。
`CONTRACT_V1_ENABLE=false` 可把 POC 冒烟环境原样恢复，用于对照排查。

`register_contract(app)` 是唯一对外入口：挂路由 + 打印生效口径；台账自检见 `ensure_contract_store`。
"""

from fastapi import FastAPI

from app.config.setting import settings
from app.core.logger import logger


def register_contract(app: FastAPI) -> None:
    """挂载正式 `/v1` 契约路由（CONTRACT_V1_ENABLE=false 时整体跳过）。"""
    if not settings.CONTRACT_V1_ENABLE:
        logger.info("==== 契约 /v1 未启用（CONTRACT_V1_ENABLE=false）====")
        return

    from app.contract.router import contract_router

    app.include_router(contract_router)
    logger.info(
        "==== 契约 /v1 已挂载：20 条骨架路由 client_id={} 时间窗 ±{}ms nonce TTL {}s ====",
        settings.CONTRACT_V1_CLIENT_ID,
        settings.CONTRACT_CLOCK_SKEW_MS,
        settings.CONTRACT_NONCE_TTL_SECONDS,
    )
    if not settings.CONTRACT_V1_SHARED_SECRET:
        # 不阻断启动（其余 /api/v1 业务照常可用），但把后果写清楚：/v1 会一律 503
        logger.warning(
            "==== CONTRACT_V1_SHARED_SECRET 未配置：/v1 全部接口将返回 503 AUTHORIZATION_UNAVAILABLE ===="
        )


async def ensure_contract_store() -> None:
    """启动自检：清理过期 nonce 与过期幂等台账（失败只告警，不影响主业务启动）。"""
    if not settings.CONTRACT_V1_ENABLE:
        return
    from app.contract.deps import contract_store_ready

    await contract_store_ready()
