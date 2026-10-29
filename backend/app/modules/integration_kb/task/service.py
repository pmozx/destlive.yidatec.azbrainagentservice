"""知识任务服务：台账查询、解析任务登记与失败节点重试（任务执行见 ``worker.py``）。

本域不使用 APScheduler：任务只在提交它的进程内执行，台账表只回答「谁在跑、跑了多久、成没成功」。
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_schema import PageResultSchema
from app.core.database import async_session_factory
from app.core.exceptions import CustomException
from app.core.logger import logger
from app.utils.common_util import search_to_dict

from ..document.crud import IntegrationKbDocumentCRUD
from ..document.model import DocumentStatus
from ..space.crud import IntegrationKbSpaceCRUD
from .crud import IntegrationKbTaskCRUD, IntegrationKbTaskStepCRUD
from .model import STAGE_LABELS, StepStage, TaskStatus, TaskType
from .schema import (
    TaskCreateSchema,
    TaskDetailOutSchema,
    TaskOutSchema,
    TaskQueryParam,
    TaskStepCreateSchema,
    TaskStepOutSchema,
)
from .worker import STAGE_START_PROGRESS, ParseJob, running_document_ids, submit_parse


class IntegrationKbTaskService:
    """知识任务服务"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.crud = IntegrationKbTaskCRUD(db)
        self.step_crud = IntegrationKbTaskStepCRUD(db)

    async def page(
        self,
        page_no: int,
        page_size: int,
        search: TaskQueryParam | None = None,
        order_by: list[dict[str, str]] | None = None,
    ) -> PageResultSchema[TaskOutSchema]:
        """分页查询任务台账：默认按 id 降序（最新提交在前）。"""
        return await self.crud.page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=order_by or [{"id": "desc"}],
            search=search_to_dict(search),
            out_schema=TaskOutSchema,
        )

    async def detail(self, id: int) -> TaskDetailOutSchema:
        """任务详情：台账 + 四段节点明细（前端时间轴据此渲染）。"""
        obj = await self.crud.get_or_404(id=id, msg="任务不存在")
        steps = await self.step_crud.get_by_task(obj.id)
        detail = TaskDetailOutSchema.model_validate(obj)
        detail.steps = [TaskStepOutSchema.model_validate(step) for step in steps]
        return detail

    async def create_parse_task(self, space_id: int, doc_id: int) -> TaskOutSchema:
        """登记一条文档解析任务，并预置四个节点（待执行），与文档落库同事务。"""
        obj = await self.crud.create(
            data=TaskCreateSchema(task_type=int(TaskType.PARSE), space_id=space_id, doc_id=doc_id)
        )
        for seq, stage in enumerate(StepStage, start=1):
            await self.step_crud.create(
                data=TaskStepCreateSchema(
                    task_id=obj.id,
                    doc_id=doc_id,
                    space_id=space_id,
                    stage=int(stage),
                    seq=seq,
                )
            )
        return TaskOutSchema.model_validate(obj)

    async def retry(self, id: int) -> TaskOutSchema:
        """从失败节点原地续跑：解析产物与分片快照都复用，只补跑失败节点及其之后的部分。

        为什么重置要另起短事务并显式提交：解析作业跑在请求之外，若在请求事务提交前投递，
        worker 会读到旧的失败状态、甚至抢在步骤重置之前开跑；先提交再投递才具备确定性。
        """
        task = await self.crud.get_or_404(id=id, msg="解析任务不存在")
        if task.status == int(TaskStatus.RUNNING):
            raise CustomException(msg="任务正在执行中，请等待本次执行结束后再重试")
        if task.status == int(TaskStatus.SUCCESS):
            raise CustomException(msg="任务已成功，无需重试")
        if task.doc_id in running_document_ids():
            raise CustomException(msg="该文档正在解析中，请等待解析结束后再重试")

        latest = await self.crud.get_latest_by_doc(task.doc_id)
        if latest is not None and latest.id != task.id:
            raise CustomException(msg="该任务不是文档的最新任务，请对最新任务重试")

        document = await IntegrationKbDocumentCRUD(self.db).get(id=task.doc_id)
        if document is None or not document.object_key:
            raise CustomException(msg="关联文档或原件缺失，无法重试，请删除后重新上传")
        space = await IntegrationKbSpaceCRUD(self.db).get(id=task.space_id)
        if space is None:
            raise CustomException(msg="所属知识空间不存在，无法重试")

        steps = await self.step_crud.get_by_task(task.id)
        failed_stages = [step.stage for step in steps if step.status == int(TaskStatus.FAILED)]
        # 最靠后的失败节点即断点：它之前的节点都成功过，重跑只会白跑一次 MinerU / 分片
        resume_stage = max(failed_stages, default=int(StepStage.PARSE))
        start_progress = STAGE_START_PROGRESS.get(resume_stage, 0)

        async with async_session_factory() as session, session.begin():
            await IntegrationKbTaskStepCRUD(session).reset_from_stage(task_id=task.id, stage=resume_stage)
            task_obj = await IntegrationKbTaskCRUD(session).update(
                id=task.id,
                data={
                    "status": int(TaskStatus.PENDING),
                    "progress": start_progress,
                    "error_msg": "",
                    "retry_count": int(task.retry_count or 0) + 1,
                    "started_time": None,
                    "finished_time": None,
                },
            )
            await IntegrationKbDocumentCRUD(session).update(
                id=document.id,
                data={"status": int(DocumentStatus.PENDING), "parse_progress": start_progress, "error_msg": ""},
            )
            result = TaskOutSchema.model_validate(task_obj)

        submit_parse(
            [
                ParseJob(
                    doc_id=document.id,
                    space_id=task.space_id,
                    space_code=space.code,
                    object_key=document.object_key,
                    file_name=document.file_name,
                    task_id=task.id,
                    resume_from=resume_stage,
                )
            ]
        )
        logger.info(
            "==== 任务重试已投递（task_id={}，doc_id={}）：自节点「{}」续跑 ====",
            task.id,
            task.doc_id,
            STAGE_LABELS.get(resume_stage, resume_stage),
        )
        return result

    async def delete(self, ids: list[int]) -> None:
        """删除任务台账记录（连同节点明细；不影响正在执行的解析）。"""
        if not ids:
            raise CustomException(msg="删除失败，删除对象不能为空")
        await self.step_crud.delete_by_tasks(ids)
        await self.crud.delete(ids=ids)
