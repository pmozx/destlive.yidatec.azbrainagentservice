"""兼容层：MinerU 客户端已迁至 ``app.core.clients.mineru``，此处只做转发。

新代码请直接引用 ``app.core.clients.mineru``，不要再依赖本模块。
"""

from app.core.clients.mineru import (
    MineruClient,
    MineruDocument,
    MineruImage,
    mineru_sdk_available,
)

__all__ = ["MineruClient", "MineruDocument", "MineruImage", "mineru_sdk_available"]
