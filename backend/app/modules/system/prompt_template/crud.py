"""提示词模板数据层。"""

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_crud import CRUDBase

from .model import SystemPromptTemplateModel


class SystemPromptTemplateCRUD(CRUDBase[SystemPromptTemplateModel, BaseModel, BaseModel]):
    """提示词模板数据层。"""

    def __init__(self, db: AsyncSession) -> None:
        super().__init__(model=SystemPromptTemplateModel, db=db)
