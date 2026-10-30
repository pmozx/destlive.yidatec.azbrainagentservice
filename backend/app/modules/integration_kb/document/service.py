"""知识文档服务：上传入库 → 投递解析 → 元数据维护 → 预览/下载 → 级联删除。

几条不可妥协的口径：
1. 去重按 (空间, 文件MD5)：同一份文件在一个空间内只留一份向量，重复上传直接拒绝；
2. 批量上传是「部分成功」语义：类型/大小/去重/对象存储写入这类可预期失败逐份记入 failed，
   不回滚已成功项；系统级异常（落库失败等）向上抛，由事务整体回滚；
3. 原件与解析产物同属「文档目录」``spaces/{空间编码}/{文档ID}/``，删文档删目录即可一并清理，
   不会留下孤儿对象；
4. 预览只读解析产物 ``parsed/full.md``（图片引用按桶内实际存在重写），PDF 另有原件直读接口；
5. 删除走物理删除 + 分片物理清理：软删后向量仍在表里，问答仍会召回已删内容。
"""

import hashlib
import io
import mimetypes
import re
import zipfile
from pathlib import PurePosixPath

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.setting import settings
from app.core.base_schema import PageResultSchema
from app.core.exceptions import CustomException
from app.core.logger import logger
from app.utils.common_util import search_to_dict

from ..category.crud import IntegrationKbCategoryCRUD
from ..chunk.vector_store import delete_chunks, vector_store_ready
from ..clients import S3StorageClient
from ..pipeline import supported_suffixes
from ..pipeline.parsed_assets import (
    IMAGE_SUFFIXES,
    image_content_type,
    parsed_content_list_key,
    parsed_image_key,
    parsed_images_prefix,
    parsed_markdown_key,
    preview_image_resolver,
    rewrite_image_refs,
    safe_image_name,
)
from ..space.crud import IntegrationKbSpaceCRUD
from ..space.model import IntegrationKbSpaceModel
from ..task.crud import IntegrationKbTaskCRUD, IntegrationKbTaskStepCRUD
from ..task.model import STAGE_LABELS, IntegrationKbTaskModel, IntegrationKbTaskStepModel, StepStage, TaskStatus
from ..task.service import IntegrationKbTaskService
from ..task.worker import ParseJob, submit_parse
from .crud import IntegrationKbDocumentCRUD
from .model import DocumentStatus, IntegrationKbDocumentModel, PipelineStatus
from .schema import (
    DocumentBatchAvailableSchema,
    DocumentDownloadSchema,
    DocumentOutSchema,
    DocumentPreviewOutSchema,
    DocumentQueryParam,
    DocumentUpdateSchema,
    DocumentUploadFailSchema,
    DocumentUploadOutSchema,
)

# 文档列表排序白名单（_parse_order 直接按字段名反射模型列，需防任意字段注入）
_ORDER_BY_FIELDS = frozenset({"id", "title", "file_size", "status", "created_time", "updated_time"})

# 可直接用原件预览的文本类型（iframe 内联展示）；docx/xlsx 等需下载后查看
_TEXT_TYPES = frozenset({"md", "markdown", "txt", "csv", "log"})

# 任务状态 → 文档流水线状态（取消按失败回显）
_PIPELINE_STATUS_BY_TASK: dict[int, int] = {
    int(TaskStatus.PENDING): int(PipelineStatus.PENDING),
    int(TaskStatus.RUNNING): int(PipelineStatus.RUNNING),
    int(TaskStatus.SUCCESS): int(PipelineStatus.DONE),
    int(TaskStatus.FAILED): int(PipelineStatus.FAILED),
    int(TaskStatus.CANCELED): int(PipelineStatus.FAILED),
}


def _resolve_stage(steps: list[IntegrationKbTaskStepModel]) -> int:
    """从步骤明细（按 seq 升序）推导「当前节点」。

    执行中节点优先；否则取失败节点（有值说明停在那里）；再否则取最后一个已完成节点；
    全未开始时取第一个待执行节点。没有任何步骤行时返回 0（未开始）。
    """
    for status in (TaskStatus.RUNNING, TaskStatus.FAILED):
        matched = [step for step in steps if step.status == int(status)]
        if matched:
            return matched[-1].stage
    done = [step for step in steps if step.status == int(TaskStatus.SUCCESS)]
    if done:
        return done[-1].stage
    pending = [step for step in steps if step.status == int(TaskStatus.PENDING)]
    return pending[0].stage if pending else (steps[-1].stage if steps else 0)


def _document_dir(space_code: str, doc_id: int) -> str:
    """文档目录前缀：原件与解析产物都落在它下面（删除时整目录清理）。"""
    parts = [settings.S3_KEY_PREFIX.strip("/"), "spaces", space_code, str(doc_id)]
    return "/".join(part for part in parts if part)


class IntegrationKbDocumentService:
    """知识文档服务（无 RBAC：调用方即管理端）"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.crud = IntegrationKbDocumentCRUD(db)

    # ── 查询 ──────────────────────────────────────────────────────────

    async def page(
        self,
        page_no: int,
        page_size: int,
        search: DocumentQueryParam | None = None,
        order_by: list[dict[str, str]] | None = None,
    ) -> PageResultSchema[DocumentOutSchema]:
        """分页查询文档（默认按 id 降序，最新上传在前；填充空间/分类名称）。"""
        result = await self.crud.page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=self._sanitize_order_by(order_by) or [{"id": "desc"}],
            search=search_to_dict(search),
            out_schema=DocumentOutSchema,
        )
        await self._fill_relation_names(items=result.items)
        await self._fill_pipeline_state(items=result.items)
        return result

    async def detail(self, id: int) -> DocumentOutSchema:
        """文档详情（填充空间/分类名称与当前流水线节点）。"""
        document = await self.crud.get_or_404(id=id, msg="知识文档不存在")
        item = DocumentOutSchema.model_validate(document)
        await self._fill_relation_names(items=[item])
        await self._fill_pipeline_state(items=[item])
        return item

    # ── 上传 ──────────────────────────────────────────────────────────

    async def upload(
        self,
        files: list[UploadFile],
        space_id: int,
        category_id: int | None,
    ) -> DocumentUploadOutSchema:
        """批量上传文档：逐份校验 → 去重 → 对象存储 → 落库 → 登记任务 → 投递解析。

        解析任务在本批全部处理完后统一投递，避免「前一份还没落库、后一份已开始解析」的抖动。
        """
        space = await IntegrationKbSpaceCRUD(self.db).get_or_404(id=space_id, msg="上传失败，所属知识空间不存在")
        category_name: str | None = None
        if category_id is not None:
            category = await IntegrationKbCategoryCRUD(self.db).get(id=category_id)
            if category is None or category.space_id != space_id:
                raise CustomException(msg="上传失败，分类不存在或不属于目标空间")
            category_name = category.name

        if not files:
            raise CustomException(msg="请选择要上传的文件")
        if len(files) > settings.SPACE_UPLOAD_MAX_FILES:
            raise CustomException(msg=f"上传失败，单次最多上传 {settings.SPACE_UPLOAD_MAX_FILES} 个文件")

        result = DocumentUploadOutSchema()
        jobs: list[ParseJob] = []
        seen_md5: set[str] = set()
        for file in files:
            try:
                item, job = await self._upload_one(
                    file=file,
                    space=space,
                    category_id=category_id,
                    category_name=category_name,
                    seen_md5=seen_md5,
                )
            except CustomException as exc:
                result.failed.append(DocumentUploadFailSchema(file_name=file.filename or "", reason=exc.msg))
                continue
            result.documents.append(item)
            jobs.append(job)

        result.success_count = len(result.documents)
        result.failed_count = len(result.failed)
        if jobs:
            submit_parse(jobs)
        return result

    async def _upload_one(
        self,
        *,
        file: UploadFile,
        space: IntegrationKbSpaceModel,
        category_id: int | None,
        category_name: str | None,
        seen_md5: set[str],
    ) -> tuple[DocumentOutSchema, ParseJob]:
        """处理单个上传文件（可预期失败抛 CustomException，由批次上层记入 failed）。"""
        if not file or not file.filename:
            raise CustomException(msg="请选择要上传的文件")
        file_name = PurePosixPath(file.filename.replace("\\", "/")).name
        if not file_name or ".." in file_name:
            raise CustomException(msg="文件名包含非法字符")

        suffix = PurePosixPath(file_name).suffix.lower()
        allowed = supported_suffixes()
        if suffix not in allowed:
            raise CustomException(
                msg=f"文件类型不支持（{suffix or '无后缀'}），允许的类型：{'、'.join(allowed)}",
                status_code=415,
            )

        content = await file.read()
        if not content:
            raise CustomException(msg="上传失败，文件内容为空")
        max_bytes = settings.SPACE_UPLOAD_MAX_SIZE_MB * 1024 * 1024
        if len(content) > max_bytes:
            raise CustomException(
                msg=f"上传失败，文件大小超过 {settings.SPACE_UPLOAD_MAX_SIZE_MB}MB",
                status_code=413,
            )

        file_md5 = hashlib.md5(content).hexdigest()
        if file_md5 in seen_md5:
            raise CustomException(msg="上传失败，与本批次内其他文件内容重复")
        if await self.crud.get_by_md5(space.id, file_md5):
            raise CustomException(msg="上传失败，相同内容的文件已存在于当前空间")
        seen_md5.add(file_md5)

        document = await self.crud.create(
            data={
                "space_id": space.id,
                "category_id": category_id,
                "title": PurePosixPath(file_name).stem or file_name,
                "file_name": file_name,
                "file_type": suffix.lstrip("."),
                "file_size": len(content),
                "file_md5": file_md5,
                "status": int(DocumentStatus.PENDING),
                "is_enabled": True,
            }
        )

        object_key = f"{_document_dir(space.code, document.id)}/{file_name}"
        try:
            storage = S3StorageClient()
            await storage.put_object(
                object_key,
                content,
                content_type=mimetypes.guess_type(file_name)[0] or "application/octet-stream",
            )
            document = await self.crud.update(id=document.id, data={"object_key": object_key})
            await IntegrationKbTaskService(self.db).create_parse_task(space_id=space.id, doc_id=document.id)
        except Exception as exc:
            # 原件没写进去就没有解析的可能：把刚建的行清掉，避免留下永远待解析的空文档
            await self.crud.delete(ids=[document.id])
            raise CustomException(msg=f"上传失败，文件写入对象存储异常：{exc}") from exc

        item = DocumentOutSchema.model_validate(document)
        item.space_name = space.name
        item.category_name = category_name
        # 刚登记任务、尚未投递解析：回显「待执行 + 第一个节点（解析）」
        item.pipeline_stage = int(StepStage.PARSE)
        item.pipeline_stage_label = STAGE_LABELS[int(StepStage.PARSE)]
        item.pipeline_status = int(PipelineStatus.PENDING)
        job = ParseJob(
            doc_id=document.id,
            space_id=space.id,
            space_code=space.code,
            object_key=object_key,
            file_name=file_name,
        )
        return item, job

    # ── 维护 ──────────────────────────────────────────────────────────

    async def update(self, id: int, data: DocumentUpdateSchema) -> DocumentOutSchema:
        """修改文档元数据（分类变更须归属文档所在空间）。"""
        document = await self.crud.get_or_404(id=id, msg="修改失败，知识文档不存在")
        updates = data.model_dump(exclude_unset=True, exclude_none=True)
        if updates.get("category_id") is not None:
            category = await IntegrationKbCategoryCRUD(self.db).get(id=updates["category_id"])
            if category is None or category.space_id != document.space_id:
                raise CustomException(msg="修改失败，分类不存在或不属于文档所在空间")
        await self.crud.update(id=id, data=data)
        return await self.detail(id=id)

    async def batch_set_available(self, data: DocumentBatchAvailableSchema) -> None:
        """批量上下架（下架文档不参与检索召回）。"""
        documents = await self.crud.get_list(search={"id": ("in", data.ids)})
        if len(documents) != len(set(data.ids)):
            raise CustomException(msg="批量设置失败，存在不存在的知识文档")
        await self.crud.set(ids=data.ids, is_enabled=data.is_enabled)

    async def reparse(self, id: int):
        """重新解析：重置解析状态 → 登记任务 → 投递（返回任务台账供前端轮询）。"""
        document = await self.crud.get_or_404(id=id, msg="重新解析失败，知识文档不存在")
        if document.status == int(DocumentStatus.PARSING):
            raise CustomException(msg="该文档正在解析中，请稍后再试")
        if not document.object_key:
            raise CustomException(msg="重新解析失败，文档原件缺失，请删除后重新上传")
        space = await IntegrationKbSpaceCRUD(self.db).get(id=document.space_id)
        if space is None:
            raise CustomException(msg="重新解析失败，所属知识空间不存在")

        await self.crud.update(
            id=id,
            data={"status": int(DocumentStatus.PENDING), "parse_progress": 0, "error_msg": ""},
        )
        task = await IntegrationKbTaskService(self.db).create_parse_task(space_id=space.id, doc_id=id)
        submit_parse(
            [
                ParseJob(
                    doc_id=document.id,
                    space_id=space.id,
                    space_code=space.code,
                    object_key=document.object_key,
                    file_name=document.file_name,
                )
            ]
        )
        return task

    async def delete(self, ids: list[int]) -> None:
        """删除文档：物理删除行 + 物理清理分片 + 清理文档目录（原件与解析产物）。"""
        if not ids:
            raise CustomException(msg="删除失败，删除对象不能为空")
        documents = await self.crud.get_list(search={"id": ("in", ids)})
        if len(documents) != len(set(ids)):
            raise CustomException(msg="删除失败，存在不存在的知识文档")

        space_map = await self._space_code_map({document.space_id for document in documents})
        for document in documents:
            await self._purge_assets(document, space_map.get(document.space_id, ""))
        # 步骤明细随文档一起清：文档没了，节点记录再无归属
        await IntegrationKbTaskStepCRUD(self.db).delete_by_docs(ids)
        await self.crud.delete(ids=ids)

    # ── 预览与下载 ────────────────────────────────────────────────────

    async def preview(self, id: int) -> DocumentPreviewOutSchema:
        """文档预览：同时给出「原件视图」与「解析产物视图」，由前端抽屉切换。

        两侧可用性独立，不能互相阻塞：
        - 原件只看类型与原件是否还在（没解析完也能看 PDF/图片/文本原件）；
        - 解析产物要等状态机到「已完成」，否则只能告以不可预览原因。
        """
        document = await self.crud.get_or_404(id=id, msg="知识文档不存在")
        file_type = (document.file_type or "").lower()
        base = DocumentPreviewOutSchema(
            doc_id=document.id,
            title=document.title,
            file_name=document.file_name,
            file_type=file_type,
            status=document.status,
            parsed_time=document.parsed_time,
        )

        # ① 原件视图
        base.source_type = self._source_type(file_type, has_object=bool(document.object_key))
        if base.source_type == "unsupported":
            base.source_message = "该类型暂不支持在线预览原件，请下载后查看" if document.object_key else "原件缺失，无法预览"

        # ② 解析产物视图
        if document.status != int(DocumentStatus.DONE):
            base.parsed_message = "文档尚未解析完成，暂无解析产物"
        else:
            try:
                base.markdown = await self.render_markdown(document)
                base.parsed_type = "markdown"
            except CustomException as exc:
                base.parsed_message = exc.msg

        # ③ 默认视图：PDF/图片优先看原件（版式最真实），否则优先看解析产物
        if base.source_type in {"pdf", "image"}:
            base.preview_type = base.source_type
        elif base.parsed_type == "markdown":
            base.preview_type = "markdown"
        elif base.source_type == "text":
            base.preview_type = "text"
        else:
            base.preview_type = "unsupported"
        base.message = base.parsed_message if base.preview_type == "unsupported" else None
        return base

    @staticmethod
    def _source_type(file_type: str, *, has_object: bool) -> str:
        """原件可直接预览的类型：pdf / image / text；其余需下载（原件缺失也归不可预览）。"""
        if not has_object:
            return "unsupported"
        if file_type == "pdf":
            return "pdf"
        if f".{file_type}" in IMAGE_SUFFIXES:
            return "image"
        if file_type in _TEXT_TYPES:
            return "text"
        return "unsupported"

    async def render_markdown(self, document: IntegrationKbDocumentModel) -> str:
        """读取解析产物 full.md 并把图片引用重写为图片读取接口地址。"""
        space = await IntegrationKbSpaceCRUD(self.db).get(id=document.space_id)
        if space is None:
            raise CustomException(msg="所属知识空间不存在，无法读取解析产物")
        storage = S3StorageClient()
        if not storage.configured:
            raise CustomException(msg="解析产物读取失败，对象存储未配置")
        key = parsed_markdown_key(space.code, document.id)
        try:
            raw = await storage.get_object(key)
        except CustomException:
            raise CustomException(msg="该文档暂无解析产物，请等待解析完成或重新解析", status_code=404) from None
        markdown = raw.decode("utf-8", errors="replace")
        names = await self.parsed_image_names(storage, space.code, document.id)
        if not names:
            return markdown
        return rewrite_image_refs(markdown, preview_image_resolver(document.id, names))

    @staticmethod
    async def parsed_image_names(storage: S3StorageClient, space_code: str, doc_id: int) -> list[str]:
        """列出解析产物图片目录下的合法图片文件名（列举失败只告警，预览保留相对引用）。"""
        try:
            keys = await storage.list_keys(parsed_images_prefix(space_code, doc_id))
        except Exception as exc:
            logger.warning("解析图片列举失败（doc_id={}）：{}", doc_id, exc)
            return []
        names: list[str] = []
        for key in keys:
            name = PurePosixPath(key).name
            if safe_image_name(name) and name not in names:
                names.append(name)
        return names

    async def read_source(self, document: IntegrationKbDocumentModel) -> tuple[bytes, str]:
        """读取原件字节（原件预览：pdf / 纯文本走 iframe，图片走 <img>）。"""
        file_type = (document.file_type or "").lower()
        if file_type == "pdf":
            media_type = "application/pdf"
        elif file_type in {"md", "markdown"}:
            media_type = "text/markdown; charset=utf-8"
        elif file_type in _TEXT_TYPES:
            media_type = "text/plain; charset=utf-8"
        elif f".{file_type}" in IMAGE_SUFFIXES:
            media_type = image_content_type(document.file_name or f"preview.{file_type}")
        else:
            raise CustomException(msg="该文档类型暂不支持原件预览", status_code=415)
        if not document.object_key:
            raise CustomException(msg="原件缺失，无法预览", status_code=404)
        data = await S3StorageClient().get_object(document.object_key)
        return data, media_type

    async def read_parsed_image(self, id: int, name: str) -> tuple[bytes, str]:
        """读取解析产物内嵌图片（Markdown 预览图片的服务端来源）。"""
        document = await self.crud.get_or_404(id=id, msg="知识文档不存在")
        safe_name = safe_image_name(name)
        if not safe_name or safe_name != name:
            raise CustomException(msg="图片资源不存在", status_code=404)
        space = await IntegrationKbSpaceCRUD(self.db).get(id=document.space_id)
        if space is None:
            raise CustomException(msg="图片资源不存在", status_code=404)
        try:
            data = await S3StorageClient().get_object(parsed_image_key(space.code, document.id, safe_name))
        except CustomException:
            raise CustomException(msg="图片资源不存在或已被清理", status_code=404) from None
        return data, image_content_type(safe_name)

    async def source(self, id: int) -> tuple[bytes, str, str]:
        """原件直读（PDF / Markdown 预览），返回 (字节, MIME, 文件名)。"""
        document = await self.crud.get_or_404(id=id, msg="知识文档不存在")
        data, media_type = await self.read_source(document)
        return data, media_type, document.file_name

    async def image(self, id: int, name: str) -> tuple[bytes, str, str]:
        """解析产物图片直读，返回 (字节, MIME, 文件名)。"""
        data, media_type = await self.read_parsed_image(id, name)
        return data, media_type, name

    async def download(self, data: DocumentDownloadSchema) -> tuple[bytes, str]:
        """批量打包下载：按勾选项把原件/解析 Markdown/版面 JSON/图片写入 zip。"""
        ids = list(dict.fromkeys(data.ids))
        documents = await self.crud.get_list(search={"id": ("in", ids)})
        doc_map = {document.id: document for document in documents}
        for did in ids:
            if did not in doc_map:
                raise CustomException(msg=f"下载失败，知识文档不存在：id={did}")
        if not (data.with_source or data.with_markdown or data.with_json):
            raise CustomException(msg="下载失败，请至少勾选一项下载内容")

        space_map = await self._space_code_map({document.space_id for document in documents})
        storage = S3StorageClient()
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for did in ids:
                document = doc_map[did]
                await self._add_doc_artifacts(
                    archive, storage, document, space_map.get(document.space_id, ""), data
                )
        return buffer.getvalue(), self._zip_name(doc_map, ids)

    async def _add_doc_artifacts(
        self,
        archive: zipfile.ZipFile,
        storage: S3StorageClient,
        document: IntegrationKbDocumentModel,
        space_code: str,
        data: DocumentDownloadSchema,
    ) -> None:
        """把单个文档被勾选的产物写入 zip（置于 {标题}/ 子目录，避免跨文档同名冲突）。"""
        folder = self._safe_name(document.title) or f"doc_{document.id}"
        if data.with_source:
            if not document.object_key:
                raise CustomException(msg=f"文档「{document.title}」原件缺失，无法下载")
            source = await storage.get_object(document.object_key)
            archive.writestr(f"{folder}/{document.file_name}", source)
        if data.with_markdown:
            if not space_code:
                raise CustomException(msg=f"文档「{document.title}」所属空间信息缺失，无法下载解析产物")
            try:
                markdown = await storage.get_object(parsed_markdown_key(space_code, document.id))
            except CustomException:
                raise CustomException(msg=f"文档「{document.title}」暂无解析 Markdown，请等待解析完成") from None
            archive.writestr(f"{folder}/full.md", markdown)
            # full.md 内为 images/ 相对引用，图片一并打包才能解压即看
            for name in await self.parsed_image_names(storage, space_code, document.id):
                try:
                    blob = await storage.get_object(parsed_image_key(space_code, document.id, name))
                except CustomException:
                    logger.warning("文档 {} 解析图片读取失败（下载跳过）：{}", document.id, name)
                    continue
                archive.writestr(f"{folder}/images/{name}", blob)
        if data.with_json:
            if not space_code:
                raise CustomException(msg=f"文档「{document.title}」所属空间信息缺失，无法下载解析产物")
            try:
                content_list = await storage.get_object(parsed_content_list_key(space_code, document.id))
            except CustomException:
                raise CustomException(msg=f"文档「{document.title}」暂无版面 JSON（可能为轻量解析模式）") from None
            archive.writestr(f"{folder}/content_list.json", content_list)

    # ── 内部辅助 ──────────────────────────────────────────────────────

    async def _purge_assets(self, document: IntegrationKbDocumentModel, space_code: str) -> None:
        """清理单文档的分片与对象存储目录（失败只告警：删除不能因外部依赖半途中断）。"""
        if vector_store_ready():
            try:
                await delete_chunks(doc_ids=[document.id])
            except Exception:
                logger.warning("删除文档：清理分片失败（doc_id={}）", document.id, exc_info=True)
        storage = S3StorageClient()
        if not storage.configured or not space_code:
            return
        try:
            await storage.delete_prefix(f"{_document_dir(space_code, document.id)}/")
        except Exception:
            logger.warning("删除文档：清理对象存储失败（doc_id={}）", document.id, exc_info=True)

    async def _space_code_map(self, space_ids: set[int]) -> dict[int, str]:
        """批量取「空间ID → 空间编码」（产物键按编码归档）。"""
        if not space_ids:
            return {}
        spaces = await IntegrationKbSpaceCRUD(self.db).get_list(search={"id": ("in", list(space_ids))})
        return {space.id: space.code for space in spaces}

    async def _fill_relation_names(self, items: list[DocumentOutSchema]) -> None:
        """批量填充 space_name / category_name（两趟查询，避免 N+1）。"""
        if not items:
            return
        space_ids = {item.space_id for item in items}
        category_ids = {item.category_id for item in items if item.category_id}
        space_map: dict[int, str] = {}
        if space_ids:
            spaces = await IntegrationKbSpaceCRUD(self.db).get_list(search={"id": ("in", list(space_ids))})
            space_map = {space.id: space.name for space in spaces}
        category_map: dict[int, str] = {}
        if category_ids:
            categories = await IntegrationKbCategoryCRUD(self.db).get_list(search={"id": ("in", list(category_ids))})
            category_map = {category.id: category.name for category in categories}
        for item in items:
            item.space_name = space_map.get(item.space_id)
            if item.category_id is not None:
                item.category_name = category_map.get(item.category_id)

    async def _fill_pipeline_state(self, items: list[DocumentOutSchema]) -> None:
        """填充「当前流水线节点 + 流水线状态」，取该文档最新一条任务推导。

        两次批量查询（任务、步骤）避免 N+1；文档没有任务台账时保持默认值 0（未开始）。
        """
        doc_ids = [item.id for item in items if item.id]
        if not doc_ids:
            return
        tasks = await IntegrationKbTaskCRUD(self.db).get_list(search={"doc_id": ("in", doc_ids)}, order_by=[{"id": "desc"}])
        latest: dict[int, IntegrationKbTaskModel] = {}
        for task in tasks:
            latest.setdefault(task.doc_id, task)
        if not latest:
            return
        steps = await IntegrationKbTaskStepCRUD(self.db).get_by_tasks([task.id for task in latest.values()])
        step_map: dict[int, list[IntegrationKbTaskStepModel]] = {}
        for step in steps:
            step_map.setdefault(step.task_id, []).append(step)
        for item in items:
            task = latest.get(item.id)
            if task is None:
                continue
            stage = _resolve_stage(step_map.get(task.id, []))
            if stage == 0 and task.status == int(TaskStatus.SUCCESS):
                # 步骤表上线前完成的历史文档没有节点明细，按「已到最后一个节点」回显
                stage = int(StepStage.WRITE)
            item.pipeline_stage = stage
            item.pipeline_stage_label = STAGE_LABELS.get(stage, "")
            item.pipeline_status = _PIPELINE_STATUS_BY_TASK.get(task.status, int(PipelineStatus.PENDING))

    @staticmethod
    def _sanitize_order_by(order_by: list[dict[str, str]] | None) -> list[dict[str, str]] | None:
        """过滤排序字段白名单并归一化方向；全非法剔除后返回 None（回落默认排序）。"""
        if not order_by:
            return order_by
        sanitized: list[dict[str, str]] = []
        for item in order_by:
            for field, direction in item.items():
                if field in _ORDER_BY_FIELDS:
                    sanitized.append({field: "desc" if str(direction).lower() == "desc" else "asc"})
        return sanitized or None

    @staticmethod
    def _zip_name(doc_map: dict[int, IntegrationKbDocumentModel], ids: list[int]) -> str:
        """zip 文件名：单文档用 {标题}.zip，多文档用「知识文档包.zip」。"""
        if len(ids) == 1:
            return f"{IntegrationKbDocumentService._safe_name(doc_map[ids[0]].title)}.zip"
        return "知识文档包.zip"

    @staticmethod
    def _safe_name(title: str) -> str:
        """把标题清洗为可用作 zip 目录名的字面量。"""
        cleaned = re.sub(r'[\\/:*?"<>|]', "_", title or "").strip()
        return cleaned or "document"
