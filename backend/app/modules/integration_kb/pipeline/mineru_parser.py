"""兼容层：MinerU 解析器已迁至 ``app.core.rag.mineru``，此处只做转发。

新代码请直接引用 ``app.core.rag.mineru``，不要再依赖本模块。
"""

from app.core.rag.mineru import MINERU_SUFFIXES, MineruDocumentParser, install_mineru_parser

__all__ = ["MINERU_SUFFIXES", "MineruDocumentParser", "install_mineru_parser"]
