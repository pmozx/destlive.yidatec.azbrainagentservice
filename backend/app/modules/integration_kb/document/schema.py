from datetime import datetime

from pydantic import BaseModel, Field

from app.core.base_schema import BaseQueryParam, BaseSchema
from app.core.validator import DateTimeStr


class DocumentUpdateSchema(BaseModel):
    """文档修改参数（空间不可改：分片与产物均按原空间归档）"""

    title: str | None = Field(default=None, min_length=1, max_length=255, description="文档标题")
    category_id: int | None = Field(default=None, ge=1, description="所属分类ID")
    keywords: str | None = Field(default=None, max_length=255, description="关键词(逗号分隔)")
    source: str | None = Field(default=None, max_length=64, description="来源")
    version: int | None = Field(default=None, ge=1, description="版本号")
    is_enabled: bool | None = Field(default=None, description="是否上架参与检索")


class DocumentBatchAvailableSchema(BaseModel):
    """批量上下架参数"""

    ids: list[int] = Field(..., min_length=1, description="文档ID列表")
    is_enabled: bool = Field(..., description="是否上架")


class DocumentOutSchema(BaseSchema):
    """文档响应模型"""

    space_id: int = Field(..., description="所属空间ID")
    space_name: str | None = Field(default=None, description="所属空间名称")
    category_id: int | None = Field(default=None, description="所属分类ID")
    category_name: str | None = Field(default=None, description="所属分类名称")
    title: str = Field(..., description="文档标题")
    file_name: str = Field(..., description="原始文件名")
    file_type: str = Field(default="", description="文件类型")
    file_size: int = Field(default=0, description="文件大小(字节)")
    file_md5: str = Field(default="", description="文件MD5")
    keywords: str = Field(default="", description="关键词")
    source: str = Field(default="", description="来源")
    version: int = Field(default=1, description="版本号")
    status: int = Field(default=0, description="状态(0:待解析 1:解析中 2:已完成 3:失败)")
    parse_progress: int = Field(default=0, description="解析进度")
    pipeline_stage: int = Field(default=0, description="当前流水线节点(0:未开始 1:解析 2:分片 3:向量化 4:写入pgvector)")
    pipeline_stage_label: str = Field(default="", description="当前流水线节点文案")
    pipeline_status: int = Field(default=0, description="流水线状态(0:待执行 1:执行中 2:已完成 3:失败)")
    error_msg: str = Field(default="", description="失败原因")
    chunk_count: int = Field(default=0, description="分片条数")
    ref_count: int = Field(default=0, description="被引用次数")
    parsed_chars: int = Field(default=0, description="解析字符数")
    elapsed_ms: int = Field(default=0, description="解析耗时(毫秒)")
    parsed_time: DateTimeStr | None = Field(default=None, description="解析完成时间")
    is_enabled: bool = Field(default=True, description="是否上架")


class DocumentUploadFailSchema(BaseModel):
    """单份文件失败明细（批量上传逐份报错，不回滚已成功项）"""

    file_name: str = Field(..., description="文件名")
    reason: str = Field(..., description="失败原因")


class DocumentUploadOutSchema(BaseModel):
    """批量上传结果"""

    success_count: int = Field(default=0, description="成功份数")
    failed_count: int = Field(default=0, description="失败份数")
    documents: list[DocumentOutSchema] = Field(default_factory=list, description="已创建文档")
    failed: list[DocumentUploadFailSchema] = Field(default_factory=list, description="失败明细")


class DocumentQueryParam(BaseQueryParam):
    """文档查询参数"""

    space_id: int | None = Field(None, ge=1, description="所属空间ID", json_schema_extra={"q": "eq"})
    category_id: int | None = Field(None, ge=1, description="所属分类ID", json_schema_extra={"q": "eq"})
    status: int | None = Field(None, ge=0, le=3, description="解析状态", json_schema_extra={"q": "eq"})
    file_type: str | None = Field(None, description="文件类型", json_schema_extra={"q": "eq"})
    title: str | None = Field(None, description="文档标题", json_schema_extra={"q": "like"})
    is_enabled: bool | None = Field(None, description="是否上架", json_schema_extra={"q": "eq"})


class DocumentPreviewOutSchema(BaseModel):
    """文档预览结果：原件视图 + 解析产物视图（前端抽屉按两个 tab 直接消费）

    原件由 ``/document/source/{id}`` 流式下发（pdf / 纯文本走 iframe，图片走 <img>），
    解析产物 Markdown 直接回正文（内嵌图片引用已重写为图片读取接口地址）。
    两侧可用性独立：原件不看解析状态，解析产物要等解析完成。
    """

    doc_id: int = Field(..., description="文档ID")
    title: str = Field(..., description="文档标题")
    file_name: str = Field(..., description="文件名")
    file_type: str = Field(default="", description="文件类型")
    status: int = Field(default=0, description="解析状态(0:待解析 1:解析中 2:已完成 3:失败)")
    parsed_time: datetime | None = Field(default=None, description="解析完成时间")
    # 默认视图（前端未显式切换时打开哪一侧）
    preview_type: str = Field(default="unsupported", description="默认视图(pdf/image/text/markdown/unsupported)")
    markdown: str | None = Field(default=None, description="解析产物 Markdown 正文(图片引用已重写)")
    message: str | None = Field(default=None, description="默认视图不可预览的原因")
    # 原件视图
    source_type: str = Field(default="unsupported", description="原件视图(pdf/image/text/unsupported)")
    source_message: str | None = Field(default=None, description="原件不可预览的原因")
    # 解析产物视图
    parsed_type: str = Field(default="unsupported", description="解析产物视图(markdown/unsupported)")
    parsed_message: str | None = Field(default=None, description="解析产物不可预览的原因")


class DocumentDownloadSchema(BaseModel):
    """批量下载参数（勾选要打包的产物）"""

    ids: list[int] = Field(..., min_length=1, description="文档ID列表")
    with_source: bool = Field(default=True, description="包含原件")
    with_markdown: bool = Field(default=False, description="包含解析 Markdown")
    with_json: bool = Field(default=False, description="包含解析版面 JSON")
