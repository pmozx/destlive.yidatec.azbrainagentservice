"""会话消息子模块：模型 + 数据访问。"""

from .crud import KbChatMessageCRUD
from .model import KbChatMessageModel

__all__ = ["KbChatMessageCRUD", "KbChatMessageModel"]
