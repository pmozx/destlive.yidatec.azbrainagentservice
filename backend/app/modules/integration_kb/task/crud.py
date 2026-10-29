from collections.abc import Sequence

from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_crud import CRUDBase

from .model import IntegrationKbTaskModel, IntegrationKbTaskStepModel, TaskStatus
from .schema import TaskCreateSchema, TaskStepCreateSchema


class IntegrationKbTaskCRUD(CRUDBase[IntegrationKbTaskModel, TaskCreateSchema, TaskCreateSchema]):
    """知识任务台账数据层"""

    def __init__(self, db: AsyncSession) -> None:
        super().__init__(model=IntegrationKbTaskModel, db=db)

    async def get_latest_by_doc(self, doc_id: int) -> IntegrationKbTaskModel | None:
        """取某文档最近一条任务（重新解析会不断追加台账，展示最新那条）。"""
        rows = await self.get_list(search={"doc_id": ("eq", doc_id)}, order_by=[{"id": "desc"}])
        return rows[0] if rows else None


class IntegrationKbTaskStepCRUD(CRUDBase[IntegrationKbTaskStepModel, TaskStepCreateSchema, TaskStepCreateSchema]):
    """任务步骤明细数据层（只增不删，随任务台账一起清理）"""

    def __init__(self, db: AsyncSession) -> None:
        super().__init__(model=IntegrationKbTaskStepModel, db=db)

    async def get_by_task(self, task_id: int) -> Sequence[IntegrationKbTaskStepModel]:
        """取单个任务的节点明细（按节点序号升序，即解析→分片→向量化→入库）。"""
        return await self.get_list(search={"task_id": ("eq", task_id)}, order_by=[{"seq": "asc"}])

    async def get_by_tasks(self, task_ids: list[int]) -> Sequence[IntegrationKbTaskStepModel]:
        """批量取多个任务的节点明细（文档列表推导当前节点时避免 N+1）。"""
        if not task_ids:
            return []
        return await self.get_list(search={"task_id": ("in", task_ids)}, order_by=[{"seq": "asc"}])

    async def delete_by_tasks(self, task_ids: list[int]) -> None:
        """随任务台账物理删除节点明细（步骤表无 deleted_id，软删判定不成立）。"""
        if not task_ids:
            return
        await self.db.execute(delete(IntegrationKbTaskStepModel).where(IntegrationKbTaskStepModel.task_id.in_(task_ids)))
        await self.db.flush()

    async def delete_by_docs(self, doc_ids: list[int]) -> None:
        """随文档物理删除其全部步骤明细（文档删除后步骤已无归属）。"""
        if not doc_ids:
            return
        await self.db.execute(delete(IntegrationKbTaskStepModel).where(IntegrationKbTaskStepModel.doc_id.in_(doc_ids)))
        await self.db.flush()

    async def reset_from_stage(self, task_id: int, stage: int) -> None:
        """把指定节点及其后续节点重置为待执行（阶段续跑：从失败的节点原地重来）。

        之前的节点保持「已完成」，它们的耗时与产出是真实发生过的记录，不清零；
        重置只抹掉失败节点与「还没跑到」的节点上的残留状态。
        """
        await self.db.execute(
            update(IntegrationKbTaskStepModel)
            .where(IntegrationKbTaskStepModel.task_id == task_id, IntegrationKbTaskStepModel.stage >= stage)
            .values(
                status=int(TaskStatus.PENDING),
                input_count=0,
                output_count=0,
                elapsed_ms=0,
                detail="",
                error_msg="",
                started_time=None,
                finished_time=None,
            )
        )
        await self.db.flush()
