"""检索数据结构（#07 · 关键词与自然语言两条通道共用）。

- **§一 领域层 dataclass**：服务层入参/结果投影，不依赖 Pydantic，服务层可脱离 FastAPI 单测；
- **§二 管理端 Pydantic Schema**：``/kb/retrieval`` 的请求/响应模型，HTTP 校验只在这一层。

契约 #07 的 HTTP 模型仍在 ``app/modules_api/kb/knowledge/schema.py``，两边不复用：
契约带签名五头与 ``authorization`` 时效块，管理端走登录态，字段集合本就不同。
"""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.validator import ensure_non_blank


@dataclass(frozen=True)
class SearchQuery:
    """一次检索的入参（两种 ``mode`` 同构；``authorization`` 时效已由契约依赖校验）。"""

    query: str
    tenant_id: str
    user_id: str
    limit: int


@dataclass(frozen=True)
class SearchHit:
    """一条 chunk 级命中（契约 ``hits[]`` 的领域投影，字段顺序与手册一致）。"""

    resource_type: str
    resource_id: str
    publication_id: str
    record_id: str
    version_id: str
    revision: int
    knowledge_no: str
    chunk_id: str
    title: str
    snippet: str
    score: float
    page_from: int | None = None
    page_to: int | None = None


@dataclass(frozen=True)
class SearchResult:
    """检索结果：``hits`` 为空即契约的 ``no_evidence=true``。"""

    hits: tuple[SearchHit, ...]
    #: 本次授权可见的发布单元数：管理端据此区分「一条都无权看」与「有权看但库里没命中」
    authorized_publication_count: int = 0

    @property
    def no_evidence(self) -> bool:
        """无证据 = 一条都没召回；有命中但分低不算无证据（阈值由契约方判断）。"""
        return not self.hits


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# §二  管理端 Pydantic Schema（/kb/retrieval：检索调试台与通道配置）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class KbRetrievalSearchSchema(BaseModel):
    """管理端检索入参：与 #07 同构，去掉签名五头 / ``request_id`` / ``authorization`` 等调用方专属项。

    ``user_id`` 必填：检索必须先算受众团队授权，管理端没有登录态，只能显式指定「以谁的身份查」。
    """

    query: str = Field(..., min_length=1, max_length=500, description="查询词或自然语言问题")
    mode: Literal["keyword", "natural_language"] = Field(default="keyword", description="检索通道")
    tenant_id: str = Field(default="gsi-default", min_length=1, max_length=64, description="租户 ID")
    user_id: str = Field(..., min_length=1, max_length=64, description="用户主键（数字字符串），决定授权范围")
    limit: int = Field(default=10, ge=1, le=50, description="返回命中条数上限")

    @field_validator("query", "tenant_id", "user_id")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        return ensure_non_blank(value)


class KbRetrievalHitOutSchema(BaseModel):
    """一条 chunk 级命中（#07 ``hits[]`` 的管理端投影，字段顺序与契约一致）。"""

    model_config = ConfigDict(from_attributes=True)

    resource_type: str = Field(..., description="资源类型")
    resource_id: str = Field(..., description="原始资源 ID")
    publication_id: str = Field(..., description="发布单元 ID")
    record_id: str = Field(..., description="版本快照记录 ID")
    version_id: str = Field(..., description="版本 ID")
    revision: int = Field(..., description="版本号")
    knowledge_no: str = Field(..., description="知识编号")
    chunk_id: str = Field(..., description="切片 ID")
    title: str = Field(..., description="条目标题")
    snippet: str = Field(..., description="命中摘要（只取本切片正文）")
    score: float = Field(..., ge=0, le=1, description="召回相关度 0–1（模式内相对分）")
    page_from: int | None = Field(default=None, description="起始页")
    page_to: int | None = Field(default=None, description="结束页")


class KbRetrievalSearchOutSchema(BaseModel):
    """检索结果：``no_evidence`` 只在真没召回时为 true，依赖故障一律走 503。"""

    mode: str = Field(..., description="本次使用的通道")
    query: str = Field(..., description="本次查询内容")
    returned_count: int = Field(..., ge=0, description="命中条数")
    no_evidence: bool = Field(..., description="是否无证据（一条都没召回）")
    elapsed_ms: int = Field(..., ge=0, description="整条链路的耗时（毫秒）")
    authorized_publication_count: int = Field(..., ge=0, description="授权可见的发布单元数")
    hits: list[KbRetrievalHitOutSchema] = Field(default_factory=list, description="命中列表")


class KbRetrievalConfigOutSchema(BaseModel):
    """检索通道配置（只读投影）：改这些值要动环境变量并重启，页面只负责展示。"""

    embedding_model: str = Field(..., description="查询向量化模型")
    embedding_ready: bool = Field(..., description="向量化服务是否已接入（地址非空）")
    vector_dim: int = Field(..., description="向量维度，须与模型输出一致")
    rerank_model: str = Field(..., description="精排模型")
    rerank_ready: bool = Field(..., description="精排服务是否已接入；未接入时按融合序回落")
    keyword_candidate_max: int = Field(..., description="关键词通道 SQL 候选上限")
    vector_candidate_max: int = Field(..., description="向量通道候选上限")
    vector_top_k: int = Field(..., description="向量召回粗排窗口")
    vector_min_score: float = Field(..., description="向量召回余弦相似度下限")
    rerank_window: int = Field(..., description="送入精排的候选上限（同时是该通道的返回条数上限）")
    rerank_doc_chars: int = Field(..., description="送精排的单条正文字符上限（标题不截断）")
    rerank_timeout: float = Field(..., description="检索链路的精排尾段预算（秒），超时降级为融合序")
    rerank_min_score: float = Field(..., description="精排相关度绝对下限（0–1）")
    rerank_relative_ratio: float = Field(..., description="精排相对闸门：低于 top1 该比例的命中被舍弃")
    rerank_score_is_logit: bool = Field(..., description="精排服务返回 logit（true）还是 0–1 概率（false）")
    rrf_k: int = Field(..., description="RRF 融合平滑常数 k")
    snippet_length: int = Field(..., description="命中摘要长度（字符）")
