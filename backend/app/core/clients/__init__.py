"""出站 HTTP 客户端集合：聊天模型 / 向量化 / 重排序。

放在 core 层是因为知识库（向量化）与 AI 问答（检索生成）两个业务域都要用，
放任一业务域内都会迫使其反向依赖，违反「业务域之间不互相 import」的约定。

HTTP 类客户端统一的超时/重试/错误归一链见 ``base.BaseHttpClient``。
"""

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
]
