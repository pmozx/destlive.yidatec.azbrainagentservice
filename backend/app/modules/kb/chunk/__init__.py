"""知识切片子模块：模型 + 数据访问。"""

from .crud import KbChunkCRUD
from .model import KbChunkModel

__all__ = ["KbChunkCRUD", "KbChunkModel"]
