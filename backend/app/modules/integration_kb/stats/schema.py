from pydantic import BaseModel, Field


class StatsOverviewOutSchema(BaseModel):
    """知识库总览指标（文档状态口径与文档表 status 一致）"""

    space_count: int = Field(default=0, description="知识空间数")
    document_count: int = Field(default=0, description="文档总数")
    parsed_count: int = Field(default=0, description="解析完成数")
    parsing_count: int = Field(default=0, description="解析中数量")
    failed_count: int = Field(default=0, description="解析失败数")
    pending_count: int = Field(default=0, description="待解析数")
    enabled_count: int = Field(default=0, description="已上架（参与检索）数")
    chunk_count: int = Field(default=0, description="分片总数")
    total_size_mb: float = Field(default=0.0, description="文档原件总大小(MB)")


class DocumentStatusStatSchema(BaseModel):
    """文档解析状态分布（状态机可视化）"""

    status: int = Field(..., description="状态值")
    label: str = Field(..., description="状态名称")
    count: int = Field(default=0, description="文档数")


class HotDocumentStatSchema(BaseModel):
    """文档引用排行（ref_count 由问答命中引用累加）"""

    doc_id: int = Field(..., description="文档ID")
    space_id: int = Field(default=0, description="所属空间ID")
    title: str = Field(default="", description="文档标题")
    ref_count: int = Field(default=0, description="被引用次数")
    chunk_count: int = Field(default=0, description="分片条数")


class SpaceStatSchema(BaseModel):
    """空间知识量排行"""

    space_id: int = Field(..., description="空间ID")
    space_name: str = Field(default="", description="空间名称")
    doc_count: int = Field(default=0, description="文档数")
    chunk_count: int = Field(default=0, description="分片条数")


class TrendPointSchema(BaseModel):
    """按日趋势点"""

    date: str = Field(..., description="日期(YYYY-MM-DD)")
    count: int = Field(default=0, description="数量")
