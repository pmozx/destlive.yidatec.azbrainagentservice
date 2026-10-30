"""兼容层：解析器注册表已迁至 ``app.core.rag.parsers``，此处只做转发。

新代码请直接引用 ``app.core.rag.parsers``，不要再依赖本模块。
"""

from app.core.rag.parsers import (
    DocumentParser,
    HtmlTextParser,
    ParsedDocument,
    ParsedImage,
    PlainTextParser,
    parse_document,
    read_document_bytes,
    register_parser,
    registered_parser_names,
    supported_suffixes,
)

__all__ = [
    "DocumentParser",
    "HtmlTextParser",
    "ParsedDocument",
    "ParsedImage",
    "PlainTextParser",
    "parse_document",
    "read_document_bytes",
    "register_parser",
    "registered_parser_names",
    "supported_suffixes",
]
