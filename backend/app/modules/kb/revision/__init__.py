"""版本历史子模块：模型 + 数据访问。"""

from .crud import KbRevisionCRUD
from .model import KbRevisionModel

__all__ = ["KbRevisionCRUD", "KbRevisionModel"]
