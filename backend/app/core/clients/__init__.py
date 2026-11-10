"""出站客户端集合：聊天模型 / 向量化 / 重排序 / 文件下载 / 对象存储。

放在 core 层是因为知识库（向量化）与 AI 问答（检索生成）两个业务域都要用，
放任一业务域内都会迫使其反向依赖，违反「业务域之间不互相 import」的约定。

HTTP 类客户端统一的超时/重试/错误归一链见 ``base.BaseHttpClient``。

**实例生命周期**：业务侧一律用本模块的 ``get_*_client()`` 取进程级单例，不要自己 ``new``。
每 new 一个实例就多一套 httpx 连接池（且永远不会被关闭），高频接口下会把端口和内存一起耗掉；
关停时由 ``close_clients()`` 统一 ``aclose()``（见 ``app/__init__.py`` 的 lifespan）。
单例只在事件循环内取用，取的过程中没有 await，因此不需要加锁也不会出现半初始化实例。

``S3Client`` 是 boto3 的同步封装，单例在 ``s3.py`` 内维护（无需连接池，也不参与关停）；
调用方用 ``asyncio.to_thread`` 包住上传/下载这类会走网络的调用。
"""

from collections.abc import Callable
from typing import Any

from app.core.logger import logger

from .base import BaseHttpClient
from .embedding import EmbeddingClient, embedding_provider
from .llm import ChatStreamChunk, LlmClient
from .mineru import MineruClient, MineruDocument, MineruImage
from .rerank import RerankClient, RerankHit
from .s3 import S3Client, S3ClientError, get_s3_client, reset_s3_client

__all__ = [
    "BaseHttpClient",
    "ChatStreamChunk",
    "EmbeddingClient",
    "LlmClient",
    "MineruClient",
    "MineruDocument",
    "MineruImage",
    "RerankClient",
    "RerankHit",
    "S3Client",
    "S3ClientError",
    "close_clients",
    "get_embedding_client",
    "get_llm_client",
    "get_rerank_client",
    "get_s3_client",
    "reset_model_clients",
    "reset_s3_client",
]


# 进程级单例表：键是能力名，值是已构造好的客户端
_registry: dict[str, Any] = {}


def _get_or_create(name: str, factory: Callable[[], Any]) -> Any:
    """取单例，没有就构造一个（同一次事件循环内先到先得，无需加锁）。"""
    client = _registry.get(name)
    if client is None:
        client = factory()
        _registry[name] = client
    return client


def _model_config_row(model_type: str) -> Any:
    """取该类型的默认配置行（进程内快照，同步读）；表不可用或没配默认行时返回 None。

    惰性导入：registry 首次刷新时会反过来导入 modules 的 CRUD，顶层导入会把
    「core.clients → core.agents.registry → modules → core」串成包级环。
    """
    from app.core.agents.registry import get_model_config_registry

    return get_model_config_registry().default_row(model_type)


def _positive_int(value: Any) -> int | None:
    """配置表里的正整数项：类型不对就当没配（jsonb 里的数字是 int / float）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _float_or_none(value: Any) -> float | None:
    """配置表里的浮点项：类型不对就当没配，交给客户端沿用 env。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _create_embedding_client() -> EmbeddingClient:
    """嵌入客户端：配置表里的默认行优先，没有就完全沿用 env（行为不变）。"""
    from app.modules.system.model_config.model import MODEL_TYPE_EMBEDDING

    row = _model_config_row(MODEL_TYPE_EMBEDDING)
    if row is None:
        return EmbeddingClient()
    defaults = row.defaults
    client = EmbeddingClient(
        row.base_url or None,
        provider=embedding_provider(row.provider),
        api_key=row.api_key or None,
        model=row.model_name or row.key,
        timeout=_float_or_none(defaults.get("timeout")),
        batch_size=_positive_int(defaults.get("batch_size")),
        concurrency=_positive_int(defaults.get("concurrency")),
        max_retries=_positive_int(defaults.get("max_retries")),
    )
    logger.info(
        "嵌入客户端使用配置表默认行：key={} model={} base_url={}",
        row.key,
        row.model_name or row.key,
        row.base_url or "env",
    )
    return client


def _create_rerank_client() -> RerankClient:
    """重排序客户端：同样以配置表默认行为先，没有则沿用 env。"""
    from app.modules.system.model_config.model import MODEL_TYPE_RERANK

    row = _model_config_row(MODEL_TYPE_RERANK)
    if row is None:
        return RerankClient()
    client = RerankClient(
        base_url=row.base_url or None,
        api_key=row.api_key or None,
        model=row.model_name or row.key,
        timeout=_float_or_none(row.defaults.get("timeout")),
    )
    logger.info(
        "重排序客户端使用配置表默认行：key={} model={} base_url={}",
        row.key,
        row.model_name or row.key,
        row.base_url or "env",
    )
    return client


def get_embedding_client() -> EmbeddingClient:
    """进程级向量化客户端（复用 httpx 连接池；配置表变更后由写路径重置单例）。"""
    return _get_or_create("embedding", _create_embedding_client)


def get_llm_client() -> LlmClient:
    """进程级聊天模型客户端。"""
    return _get_or_create("llm", LlmClient)


def get_rerank_client() -> RerankClient:
    """进程级重排序客户端。"""
    return _get_or_create("rerank", _create_rerank_client)


async def reset_model_clients() -> None:
    """写配置后重建嵌入 / 重排序单例（连接方式存在实例上，不重建等于改了地址没生效）。

    对话客户端不在重置范围：``LlmClient`` 无状态，连接参数每次由 ``profile`` 带进来，
    配置快照过期后下一个请求自然用到新配置。
    """
    for name in ("embedding", "rerank"):
        client = _registry.pop(name, None)
        if client is None:
            continue
        aclose = getattr(client, "aclose", None)
        if aclose is None:
            continue
        try:
            await aclose()
        except Exception as e:  # noqa: BLE001 旧连接池关不掉不该让页面的保存失败
            logger.warning("重建模型客户端前关闭旧实例失败 name={}: {}", name, e)


async def close_clients() -> int:
    """关闭所有已构造的客户端，返回关闭数量（应用关停时调用）。

    关闭失败只记日志：关停阶段不该因为某个外部服务不可达而抛出异常打断后续清理。
    """
    from app.core.clients.download import close_download_clients
    from app.core.logger import logger

    closed = await close_download_clients()
    for name, client in list(_registry.items()):
        aclose = getattr(client, "aclose", None)
        if aclose is None:
            continue
        try:
            await aclose()
            closed += 1
        except Exception as e:  # noqa: BLE001 关停阶段一律只告警
            logger.warning("关闭出站客户端失败 name={}: {}", name, e)
    _registry.clear()
    return closed
