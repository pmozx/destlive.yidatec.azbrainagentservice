"""解析任务子模块：模型 + 数据访问（管理端只读路由见 ``admin_controller.py``）。"""

from .crud import KbParseJobCRUD
from .model import KbParseJobModel

__all__ = ["KbParseJobCRUD", "KbParseJobModel"]
