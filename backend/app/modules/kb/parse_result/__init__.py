"""解析产果子模块：模型 + 数据访问。"""

from .crud import KbParseResultCRUD
from .model import KbParseResultModel

__all__ = ["KbParseResultCRUD", "KbParseResultModel"]
