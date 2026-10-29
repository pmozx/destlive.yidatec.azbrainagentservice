"""文档处理流水线：解析器注册表 → 分片 → 向量化 → 写入 pgvector。"""

from .chunker import TextChunk, normalize_text, split_text
from .document_pipeline import (
    ArtifactSummary,
    ChunkedDocument,
    PipelineResult,
    build_embedding_rows,
    parse_and_chunk,
    persist_artifacts,
    process_document,
)
from .mineru_parser import MINERU_SUFFIXES, MineruDocumentParser, install_mineru_parser
from .page_map import build_page_map, resolve_page_no
from .parsers import (
    DocumentParser,
    ParsedDocument,
    ParsedImage,
    parse_document,
    read_document_bytes,
    register_parser,
    registered_parser_names,
    supported_suffixes,
)

__all__ = [
    "MINERU_SUFFIXES",
    "ArtifactSummary",
    "ChunkedDocument",
    "DocumentParser",
    "MineruDocumentParser",
    "ParsedDocument",
    "ParsedImage",
    "PipelineResult",
    "TextChunk",
    "build_embedding_rows",
    "build_page_map",
    "install_mineru_parser",
    "normalize_text",
    "parse_and_chunk",
    "parse_document",
    "persist_artifacts",
    "process_document",
    "read_document_bytes",
    "register_parser",
    "registered_parser_names",
    "resolve_page_no",
    "split_text",
    "supported_suffixes",
]
