"""RAG 核心能力：解析 → 分片 → 向量化 → 向量入库。

分层约定：
- ``parsers`` / ``mineru``：文件解析与可插拔解析器；
- ``pipeline``：编排（同步解析与分片走线程池，向量化走共享客户端）；
- ``vector_store``：通用向量层（表 ``sbp_ai_kb_vector_collection`` / ``sbp_ai_kb_vector_embedding``，
  原生 SQL 自持表名与索引名），业务模型由调用方注入，core 不 import modules；当前仅 ``validate_embedding`` 有调用方；
- 纯文本切片与页码定位放在 ``app.utils``（``text_splitter`` / ``page_locator``），core 复用。
"""

from .pipeline import (
    ChunkedDocument,
    PipelineResult,
    STAGE_CHUNK,
    STAGE_EMBED,
    STAGE_PARSE,
    STAGE_WRITE,
    build_embedding_rows,
    embed_chunks,
    parse_and_chunk,
    process_document,
)
from .parsers import (
    DocumentParser,
    HtmlTextParser,
    ParsedDocument,
    ParsedImage,
    PlainTextParser,
    parse_document,
    register_parser,
    registered_parser_names,
    supported_suffixes,
)

__all__ = [
    "ChunkedDocument",
    "DocumentParser",
    "HtmlTextParser",
    "ParsedDocument",
    "ParsedImage",
    "PipelineResult",
    "PlainTextParser",
    "STAGE_CHUNK",
    "STAGE_EMBED",
    "STAGE_PARSE",
    "STAGE_WRITE",
    "build_embedding_rows",
    "embed_chunks",
    "parse_and_chunk",
    "parse_document",
    "process_document",
    "register_parser",
    "registered_parser_names",
    "supported_suffixes",
]
