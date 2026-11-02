from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_crud import CRUDBase

from .model import SystemApiLogModel
from .schema import SystemApiLogCreateSchema


class SystemApiLogCRUD(CRUDBase[SystemApiLogModel, SystemApiLogCreateSchema, SystemApiLogCreateSchema]):
    """接口审计日志数据层

    审计表只追加、只查询，不存在业务更新；删除为物理删除——审计记录一旦"软删"就会出现
    「列表看不到、库里还占着」的两义状态，取证时反而说不清记录到底在不在。
    """

    def __init__(self, db: AsyncSession) -> None:
        super().__init__(model=SystemApiLogModel, db=db)
