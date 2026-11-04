"""nonce 防重放台账（T1.1）：把 POC 的进程内字典换成持久化 + 原子登记。

为什么必须落库（POC 结论 §二.1 的「待正式化」）：
1. 进程内字典在多副本部署下各存一份，重放打到另一实例就漏判；重启后清零，窗口内还能再放一次；
2. 「先查后插」在并发重放下两个请求会同时通过检查：首用的唯一事实来源必须是
   `uq_sbp_ai_system_nonce_client_nonce` 这个唯一约束；
3. 单条 SQL 完成「插入 / 已过期则接管 / 未过期则不动」，不需要额外锁：
   ``INSERT ... ON CONFLICT (client_id, nonce) DO UPDATE ... WHERE expires_at < now RETURNING id``
   返回行 = 本次首用（新插入或接管过期行）；无返回行 = 窗口内重复 = 重放。

数据访问与事务已迁入 ``app.modules.system.nonce.service.SystemNonceService``；
本文件只保留契约层适配：把台账不可用翻译成 503 ``AUTHORIZATION_UNAVAILABLE``。
"""

from sqlalchemy.exc import SQLAlchemyError

from app.modules.system.nonce.service import SystemNonceService

from .errors import AUTHORIZATION_UNAVAILABLE, ContractApiError


class NonceStore:
    """nonce 原子登记：一次往返内完成判重与写入，跨实例共享。"""

    def __init__(self, *, ttl_seconds: int | None = None) -> None:
        self._service = SystemNonceService(ttl_seconds=ttl_seconds)

    async def register(self, *, client_id: str, nonce: str, request_id: str | None, current_ms: int) -> bool:
        """首用返回 True；窗口内重复（重放）返回 False。"""
        try:
            return await self._service.register(
                client_id=client_id,
                nonce=nonce,
                request_id=request_id,
                current_ms=current_ms,
            )
        except SQLAlchemyError as exc:
            # 台账不可用时不能放行（放行等于关掉防重放），按「授权依赖不可用」503 处理
            raise ContractApiError(AUTHORIZATION_UNAVAILABLE, "签名防重放台账暂不可用，请稍后重试。") from exc

    async def purge_expired(self, *, current_ms: int | None = None) -> int:
        """物理清理过期 nonce（启动时与巡检调用），返回清理条数。"""
        return await self._service.purge_expired(current_ms=current_ms)


# 模块级单例：无状态（TTL 来自配置），依赖注入与测试都直接复用
nonce_store = NonceStore()
