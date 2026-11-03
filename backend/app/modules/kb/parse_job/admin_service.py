"""解析任务管理查询服务：列表 / 详情（管理端只读，复用契约链路已有 CRUD）。"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_schema import PageResultSchema
from app.utils.common_util import search_to_dict

from ..parse_result.crud import KbParseResultCRUD
from ..parse_result.preview import pages_with_public_base
from .crud import KbParseJobCRUD
from .schema import (
    KbParseJobDetailOutSchema,
    KbParseJobOutSchema,
    KbParseJobQueryParam,
    KbParseResultOutSchema,
)


class KbParseJobAdminService:
    """解析任务管理服务（复用请求级会话，只读查询不写状态）。"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.crud = KbParseJobCRUD(db)

    async def page(
        self,
        *,
        page_no: int,
        page_size: int,
        search: KbParseJobQueryParam | None = None,
        order_by: list[dict[str, str]] | None = None,
    ) -> PageResultSchema[KbParseJobOutSchema]:
        """分页查询解析任务：默认按创建时间倒序（最新任务在最前）。"""
        return await self.crud.page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=order_by or [{"created_time": "desc"}],
            search=search_to_dict(search),
            out_schema=KbParseJobOutSchema,
        )

    async def detail(self, job_id: str) -> KbParseJobDetailOutSchema:
        """任务详情：任务行 + （若成功）解析产物。"""
        job = await self.crud.get_or_404(uuid=job_id, msg="解析任务不存在")
        result = await KbParseResultCRUD(self.db).get(job_id=job_id)
        result_out = KbParseResultOutSchema.model_validate(result) if result else None
        if result_out is not None:
            # 管理端同样用当前对外基址，旧任务存的 127.0.0.1 地址也能直接预览
            result_out.pages = pages_with_public_base(result_out.pages)
        out = KbParseJobDetailOutSchema(
            id=job.id,
            uuid=job.uuid,
            created_time=job.created_time,
            updated_time=job.updated_time,
            resource_type=job.resource_type,
            status=job.status,
            progress=job.progress,
            error_code=job.error_code,
            error_message=job.error_message,
            file_meta=job.file_meta or {},
            result=result_out,
        )
        return out
