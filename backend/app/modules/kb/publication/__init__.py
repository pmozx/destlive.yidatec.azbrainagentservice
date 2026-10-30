"""发布单元子模块：模型 + 数据访问。"""

from .crud import KbPublicationCRUD
from .model import KbPublicationModel

__all__ = ["KbPublicationCRUD", "KbPublicationModel"]
