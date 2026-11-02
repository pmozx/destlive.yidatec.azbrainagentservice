"""知识条目元数据子模块：模型 + 数据访问。"""

from .crud import KbEntryMetaCRUD
from .model import KbEntryMetaModel

__all__ = ["KbEntryMetaCRUD", "KbEntryMetaModel"]
