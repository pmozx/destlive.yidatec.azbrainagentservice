"""出站 HTTP 客户端集合：聊天模型 / 向量化 / 重排序。

放在 core 层是因为知识库（向量化）与 AI 问答（检索生成）两个业务域都要用，
放任一业务域内都会迫使其反向依赖，违反「业务域之间不互相 import」的约定。

HTTP 类客户端统一的超时/重试/错误归一链见 ``base.BaseHttpClient``。

**实例生命周期**：业务侧一律用本模块的 ``get_*_client()`` 取进程级单例，不要自己 ``new``。
每 new 一个实例就多一套 httpx 连接池（且永远不会被关闭），高频接口下会把端口和内存一起耗掉；
关停时由 ``close_clients()`` 统一 ``aclose()``（见 ``app/__init__.py`` 的 lifespan）。
单例只在事件循环内取用，取的过程中没有 await，因此不需要加锁也不会出现半初始化实例。
"""

from typing import Any

from .base import BaseHttpClient
from .embedding import EmbeddingClient
from .llm import ChatMessage, ChatStreamChunk, LlmClient
from .mineru import MineruClient, MineruDocument, MineruImage
from .rerank import RerankClient, RerankHit

__all__ = [
    "BaseHttpClient",
    "ChatMessage",
    "ChatStreamChunk",
    "EmbeddingClient",
    "LlmClient",
    "MineruClient",
    "MineruDocument",
    "MineruImage",
    "RerankClient",
    "RerankHit",
    "close_clients",
    "get_embedding_client",
    "get_llm_client",
    "get_rerank_client",
]


# 进程级单例表：键是能力名，值是已构造好的客户端
_registry: dict[str, Any] = {}


def _get_or_create(name: str, factory: type) -> Any:
    """取单例，没有就构造一个（同一次事件循环内先到先得，无需加锁）。"""
    client = _registry.get(name)
    if client is None:
        client = factory()
        _registry[name] = client
    return client


def get_embedding_client() -> EmbeddingClient:
    """进程级向量化客户端（复用 httpx 连接池；配置变更需重启进程）。"""
    return _get_or_create("embedding", EmbeddingClient)


def get_llm_client() -> LlmClient:
    """进程级聊天模型客户端。"""
    return _get_or_create("llm", LlmClient)


def get_rerank_client() -> RerankClient:
    """进程级重排序客户端。"""
    return _get_or_create("rerank", RerankClient)


async def close_clients() -> int:
    """关闭所有已构造的客户端，返回关闭数量（应用关停时调用）。

    关闭失败只记日志：关停阶段不该因为某个外部服务不可达而抛出异常打断后续清理。
    """
    from app.core.logger import logger

    closed = 0
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
