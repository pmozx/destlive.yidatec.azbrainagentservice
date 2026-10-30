"""正式 `/v1` 契约接入层（P1：T1.1–T1.5），与 `/api/v1`（管理台 JWT）分离。

模块分工：

| 模块 | 职责 | 任务 |
| --- | --- | --- |
| `errors.py` | 契约错误码全量表 + 错误体路由类（继承审计路由类） | T1.2 |
| `signature.py` | 验签口径翻译：算法在 `app/core/security_signature.py`，本模块只把失败翻成大写的契约码 | T1.1 |
| `nonce.py` | nonce 原子登记（`sbp_ai_system_nonce`，跨实例共享 + TTL） | T1.1 |
| `idempotency.py` | 三类幂等台账（job / request / event）、异正文 409、墓碑 | T1.3 |
| `audit.py` | `security_check` JSONB 结构装载（签名只存摘要） | T1.4 |
| `deps.py` | 验签依赖、幂等预约依赖、启动自检 | T1.1 / T1.3 |
| `router.py` | 20 条路由骨架（签名可打通，业务随 Tx 落地） | T1.5 |
| `mock_gsi.py` | GSI 侧冒烟驱动器（样例文件 + 负向用例 + `--allow-skeleton`） | 联调 |

POC 验证骨架（`app/poc`）已按 `03-POC下线与契约迁移方案.md` 阶段 0 下线，
`/v1` 只有本模块一份实现；`CONTRACT_V1_ENABLE=false` 表示 `/v1` 完全不挂载（404），不再提供回退。
验签/防重放/授权时效算法在 `app/core/security_signature.py`，参数在 `setting.py` 的 `CONTRACT_*` 组。

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
    if not settings.CONTRACT_V1_SHARED_SECRET.strip():
        # 不阻断启动（其余 /api/v1 业务照常可用），但把后果写清楚：/v1 会一律 503
        logger.warning(
            "==== CONTRACT_V1_SHARED_SECRET 未配置：/v1 全部接口将返回 503 AUTHORIZATION_UNAVAILABLE ===="
        )
    if settings.CONTRACT_NONCE_TTL_SECONDS * 1000 < 2 * settings.CONTRACT_CLOCK_SKEW_MS:
        # 覆盖不足时窗口内的重放可能漏判：只告警，不改默认值（调大 TTL 属运维决策）
        logger.warning(
            "==== CONTRACT_NONCE_TTL_SECONDS={}s 小于 2×时钟窗口（{}s）：窗口内重放可能漏判 ====",
            settings.CONTRACT_NONCE_TTL_SECONDS,
            settings.CONTRACT_CLOCK_SKEW_MS * 2 // 1000,
        )


async def ensure_contract_store() -> None:
    """启动自检：清理过期 nonce 与过期幂等台账（失败只告警，不影响主业务启动）。"""
    if not settings.CONTRACT_V1_ENABLE:
        return
    from app.contract.deps import contract_store_ready

    await contract_store_ready()
