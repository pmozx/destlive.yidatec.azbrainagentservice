"""知识库专用外部服务客户端：对象存储（S3/MinIO）与 MinerU 文档解析。

模型类客户端（LLM / Embedding / Rerank）不在这里，见 ``app.core.clients``，
以便 AI 问答域共用而不产生跨域依赖。
"""

from .mineru import MineruClient, MineruDocument, mineru_sdk_available
from .s3 import S3StorageClient

__all__ = [
    "MineruClient",
    "MineruDocument",
    "S3StorageClient",
    "mineru_sdk_available",
]
