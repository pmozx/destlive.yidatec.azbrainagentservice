from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_crud import CRUDBase

from .model import OperationLogModel
from .schema import OperationLogCreateSchema


class OperationLogCRUD(CRUDBase[OperationLogModel, OperationLogCreateSchema, OperationLogCreateSchema]):
    """操作日志数据层

    审计表只追加、只查询，不存在业务更新；删除为物理删除（ModelMixin 无 deleted_id，
    CRUDBase 判定为不支持软删除，否则回收站语义会让审计记录“看着还在”）。
    """

    def __init__(self, db: AsyncSession) -> None:
        super().__init__(model=OperationLogModel, db=db)
