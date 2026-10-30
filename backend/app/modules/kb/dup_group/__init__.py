"""重复分组子模块：模型 + 数据访问。"""

from .crud import KbDupGroupCRUD
from .model import KbDupGroupModel

__all__ = ["KbDupGroupCRUD", "KbDupGroupModel"]
