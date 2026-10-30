"""提示词模板子模块：模型 + 数据访问。"""

from .crud import SystemPromptTemplateCRUD
from .model import SystemPromptTemplateModel

__all__ = ["SystemPromptTemplateCRUD", "SystemPromptTemplateModel"]
