"""模型配置表的读取与档案解析（``sbp_ai_system_model_config`` → :class:`ModelProfile`）。

为什么要有注册表这一层：配置表只有十几行，按 key 逐条查会让每轮问答都多一次数据库往返；
但把行缓存成进程级单例又必须能被页面改配置后的下一次刷新看见。折中是**全量快照 + TTL**：
一次取全、进程内按 ``model_config_key`` 索引，``LLM_CONFIG_CACHE_TTL`` 到点后重新拉一次；页面
写完配置则调 :func:`invalidate_model_config_cache` 把快照置为过期（不必等 TTL）。

快照包含全部类型的行（对话 / 嵌入 / 重排序）：对话行用于取档案，嵌入与重排序行供
``app.core.clients`` 的客户端工厂读默认行（同步工厂拿不到 await，只能读内存快照）。
:meth:`ModelConfigRegistry.find` 只认对话行——把一行 embedding 当成对话默认模型，问答会在
第一个请求时变成「模型不存在」。

可用性优先的三条回落（顺序固定）：

1. 配置表可用且命中 ``model_config_key`` → 用行里的连接方式与默认参数；
2. 未命中 → 把槽位值当**模型名**直传（兼容建表之前的 env 配置），并告警留痕；
3. 配置表不可用（库连不上 / 表还没建） → 沿用上一次快照，没有快照就走纯 env 档案。

即：**配置表是集中管理与加速，不是问答链路的硬依赖**——它挂了不该让用户看不到答案。
"""

import asyncio
import time
from dataclasses import dataclass
from typing import Any

from app.config.setting import settings
from app.core.database import async_session_factory
from app.core.logger import logger

from .profiles import ModelProfile, build_profile, profile_for, purpose_slot

#: 对话模型的类型标记（与 ``model_config.model.MODEL_TYPE_CHAT`` 一致）：
#: registry 在 core 层，顶层反向 import modules 会把「core → modules → core」串成包级环，
#: 因此这里只保留字符串；tests/core/test_model_registry.py 会断言二者一致。
CHAT_MODEL_TYPE = "chat"


@dataclass(frozen=True, slots=True)
class ModelConfigRow:
    """配置行在进程内的快照（脱离 ORM 会话，避免关连接后再触发懒加载）。"""

    key: str
    name: str
    model_type: str
    model_name: str
    provider: str
    base_url: str
    api_key: str
    defaults: dict[str, Any]
    is_default: bool


class ModelConfigRegistry:
    """配置表全量快照：异步刷新、进程内按 ``model_key`` 索引。"""

    def __init__(self) -> None:
        self._rows: dict[str, ModelConfigRow] = {}
        self._loaded_at = 0.0
        self._loaded_once = False
        self._degraded = False
        self._lock = asyncio.Lock()

    @property
    def filled(self) -> bool:
        """是否已经成功读到过配置行（决定「未命中」要不要告警）。"""
        return bool(self._rows)

    @property
    def size(self) -> int:
        """快照里的行数（启动日志与排障用，不暴露内部字典）。"""
        return len(self._rows)

    def find(self, key: str) -> ModelConfigRow | None:
        """按模型标识取**对话**行（嵌入 / 重排序行不参与档案解析）。"""
        row = self._rows.get(key) if key else None
        return row if row is not None and row.model_type == CHAT_MODEL_TYPE else None

    def default_row(self, model_type: str = CHAT_MODEL_TYPE) -> ModelConfigRow | None:
        """该类型里 ``is_default`` 的那一行（对话取档案，嵌入/重排序供客户端工厂）。"""
        for row in self._rows.values():
            if row.model_type == model_type and row.is_default:
                return row
        return None

    def default(self) -> ModelConfigRow | None:
        """对话类型的默认行（用途槽位全空时的兜底）。"""
        return self.default_row()

    def invalidate(self) -> None:
        """把快照置为过期：下一次 :meth:`ensure_loaded` 会重新查库。

        只标记不立即刷新：写配置的请求还在事务里，此时重查会读到旧值（提交发生在
        HTTP 层），下一次问答自然会拿到新配置。已缓存的旧行**不清空**，这样即时重查
        失败时仍能回落到上一次可用快照。因为没有 IO，定成同步方法——写成 async 再被
        同步调用会得到一个未 await 的协程，过期标记静默失效。
        """
        self._loaded_once = False
        self._loaded_at = 0.0

    async def ensure_loaded(self) -> None:
        """按 TTL 决定是否需要刷新；并发调用只会有一次真正查库。"""
        ttl = max(0.0, settings.LLM_CONFIG_CACHE_TTL)
        if self._loaded_once and ttl and time.monotonic() - self._loaded_at < ttl:
            return
        async with self._lock:
            if self._loaded_once and ttl and time.monotonic() - self._loaded_at < ttl:
                return
            await self._refresh()

    async def _refresh(self) -> None:
        # 延迟导入：crud 会反过来导入 schema → ``app.core.agents.profiles``，顶层导入会把
        # 「core → modules → core」串成包级环，谁先被导入就决定谁报错（ImportError 位置还很误导）
        from app.modules.system.model_config.crud import SystemModelConfigCRUD

        try:
            async with async_session_factory() as session:
                rows = await SystemModelConfigCRUD(session).list_active()
        except Exception as exc:  # noqa: BLE001 配置表不可用不能把问答打挂
            self._degraded = True
            # 退避：TTL 内不再重试，避免每个请求都在等一次数据库超时
            self._loaded_at = time.monotonic()
            self._loaded_once = True
            if not self._rows:
                logger.warning("模型配置表不可用，问答将回落 env 配置：{}", exc)
            return
        self._rows = {
            row.model_config_key: ModelConfigRow(
                key=row.model_config_key,
                name=row.model_config_name,
                model_type=row.model_type or CHAT_MODEL_TYPE,
                model_name=row.model_name,
                provider=row.model_provider or "",
                base_url=row.base_url,
                api_key=row.api_key,
                defaults=dict(row.default_config_json or {}),
                is_default=bool(row.is_default),
            )
            for row in rows
            if row.model_config_key
        }
        self._loaded_at = time.monotonic()
        self._loaded_once = True
        if self._degraded:
            logger.info("模型配置表已恢复，共 {} 行", len(self._rows))
        self._degraded = False


_registry = ModelConfigRegistry()


def get_model_config_registry() -> ModelConfigRegistry:
    """进程级注册表单例。"""
    return _registry


def reset_model_config_registry() -> None:
    """清空快照（测试用：切桩行后不必等 TTL）。"""
    global _registry
    _registry = ModelConfigRegistry()


def invalidate_model_config_cache() -> None:
    """让模型配置快照过期（页面新增/修改/删除后调用，改完即生效）。

    只打标不查库，所以可以同步调用、不必等 IO。注册表还没加载过时是空操作。
    """
    _registry.invalidate()


async def load_profile(purpose: str, *, key: str | None = None) -> ModelProfile:
    """解析用途档案：显式 ``key`` > env 槽位；配置表命中则带上连接方式与默认参数。

    ``key`` 来自调用入参（如 GSI 的 ``model_key``），指配置表的模型标识；槽位为空且未显式
    指定时退回表里 ``is_default`` 的行，这就是「表里配好、env 不用改」的上线路径。
    """
    slot = (key or "").strip() or purpose_slot(purpose)
    registry = get_model_config_registry()
    row: ModelConfigRow | None = None
    if settings.LLM_CONFIG_ENABLE:
        await registry.ensure_loaded()
        row = registry.find(slot) or (registry.default() if not key else None)

    if row is None:
        if slot and settings.LLM_CONFIG_ENABLE and registry.filled:
            logger.warning(
                "模型配置表未命中标识，按模型名直传（旧配置口径）：purpose={} key={}",
                purpose,
                slot,
            )
        return profile_for(purpose, model=slot or None)

    return build_profile(
        purpose,
        key=row.key,
        model=row.model_name or row.key,
        provider=row.provider,
        base_url=row.base_url,
        api_key=row.api_key,
        model_defaults=row.defaults,
    )


__all__ = [
    "CHAT_MODEL_TYPE",
    "ModelConfigRegistry",
    "ModelConfigRow",
    "get_model_config_registry",
    "invalidate_model_config_cache",
    "load_profile",
    "reset_model_config_registry",
]
