"""POC `/v1` 验证骨架：T0.1–T0.4 的最小实现，**不是**正式交付代码。

定位（见 `01-2-知识平台开发计划与说明.md` §七.7 与 §六 T0.x）：

1. 独立 `/v1` 前缀 = GSI 后端签名调用，不挂 JWT Bearer，与 `/api/v1`（前端与管理台）完全分离；
2. 目标是打通 `#01 → #03 → #07 → #14` 最小链路 + 四头签名冒烟，证明契约可落地、量出关键耗时；
3. 数据落 `poc_v1_*` 临时表（不走 Alembic、不进 `sbp_ai_` 命名空间），签名 nonce 只存进程内存，
   幂等只覆盖 POC 用到的三类请求 —— 以上三点在 T1.x 一律按生产标准重做，POC 代码不直接沿用；
4. 因此这里刻意不做：实时权限下推（T4.3）、审计中间件覆盖（T1.4）、三类幂等台账（T1.3）、
   页图渲染（T2.4）、修订/删除四路径（T3.2/T3.3）。

`register_poc(app)` 是唯一对外入口：挂路由 + 注册 `/v1` 专用错误体处理器。
"""

from app.config.setting import settings
from app.core.logger import logger


def register_poc(app) -> None:
    """挂载 POC `/v1` 路由（POC_V1_ENABLE=false 或契约层已开启时跳过）。

    与正式契约层**互斥**：两者都占 `/v1` 前缀，同时挂载会出现重复路由，
    且 POC 的错误码是小写、nonce 在内存里、无审计，不是正式口径。
    契约层（`CONTRACT_V1_ENABLE`）优先级更高；要回到 POC 冒烟，把它设为 false。
    """
    if settings.CONTRACT_V1_ENABLE:
        logger.info("==== POC /v1 未挂载（CONTRACT_V1_ENABLE=true，已由正式契约层接管）====")
        return
    if not settings.POC_V1_ENABLE:
        logger.info("==== POC /v1 路由未启用（POC_V1_ENABLE=false）====")
        return

    from app.poc.router import poc_router

    app.include_router(poc_router)
    logger.info(
        "==== POC /v1 已挂载：client_id={} 签名窗口 ±{}ms 临时表前缀 poc_v1_ ====",
        settings.POC_V1_CLIENT_ID,
        settings.POC_V1_CLOCK_SKEW_MS,
    )


async def ensure_poc_store() -> None:
    """启动期幂等建 POC 临时表（失败只告警，不影响主业务启动）。"""
    if not settings.POC_V1_ENABLE:
        return
    from app.poc.store import ensure_poc_tables

    await ensure_poc_tables()
