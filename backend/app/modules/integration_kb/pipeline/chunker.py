"""兼容层：文本切片能力已迁至 ``app.utils.text_splitter``，此处只做转发。

保留模块路径是为了让仍在过渡期的集成知识库代码平滑切换；新代码请直接引用
``app.utils.text_splitter``，不要再依赖本模块。
"""

from app.utils.text_splitter import TextChunk, normalize_text, split_text

__all__ = ["TextChunk", "normalize_text", "split_text"]
