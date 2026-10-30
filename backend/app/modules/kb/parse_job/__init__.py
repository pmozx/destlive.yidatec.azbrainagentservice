"""解析任务子模块：模型 + 数据访问。"""

from .crud import KbParseJobCRUD
from .model import KbParseJobModel

__all__ = ["KbParseJobCRUD", "KbParseJobModel"]
