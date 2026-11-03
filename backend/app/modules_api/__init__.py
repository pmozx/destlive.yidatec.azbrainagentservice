"""对外 API 接入域：``/v1`` 契约接口（HMAC 五头签名），与管理台 ``/api/v1``（JWT）分离。

目录约定：

```
app/modules_api/
├── router.py                  # 路由总表（唯一事实来源，F2）
└── contract/
    ├── common.py              # 路由类 / 验签依赖 / 错误响应声明 / 501 骨架出口
    ├── deps.py                # 验签、nonce 防重放、幂等预约依赖
    ├── errors.py              # 错误码全量表 + ContractApiRoute（异常 → 契约错误体）
    ├── audit.py               # security_check JSONB 装载
    ├── signature.py           # 验签口径翻译（算法在 app/core/security_signature.py）
    ├── nonce.py               # nonce 台账适配
    ├── idempotency.py         # 三类幂等域适配
    ├── schema.py              # 骨架期通用请求体
    ├── parse/                 # #01–#02
    ├── knowledge/             # #03–#07、#16–#20
    └── chat/                  # #08–#15
```

通用能力（签名算法、请求体解析、下载客户端、规范化摘要、非空校验）在 ``app/core`` 或
``app/utils``，本域只保留「知道 /v1 契约协议」的适配代码（错误码、错误体、幂等域）。
"""

from fastapi import FastAPI

from app.config.setting import settings
from app.core.logger import logger

from .router import DOMAIN_CONTROLLERS, api_router


def register_api(app: FastAPI) -> None:
    """挂载 `/v1` 契约路由（CONTRACT_V1_ENABLE=false 时整体跳过）。"""
    if not settings.CONTRACT_V1_ENABLE:
        logger.info("==== 契约 /v1 未启用（CONTRACT_V1_ENABLE=false）====")
        return

    app.include_router(api_router)
    logger.info(
        "==== 契约 /v1 已挂载：20 条路由 client_id={} 时间窗 ±{}ms nonce TTL {}s ====",
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

    # 测试辅助路由（签名头生成等）：CONTRACT_TEST_ENABLE=true 时挂载
    if settings.CONTRACT_TEST_ENABLE:
        from app.modules_api.contract.test_utils.controller import TestRouter

        api_router.include_router(TestRouter)
        logger.info("==== 测试辅助路由已挂载（/v1/test/*）：生产环境请关闭 CONTRACT_TEST_ENABLE ====")


__all__ = ["DOMAIN_CONTROLLERS", "api_router", "register_api"]
