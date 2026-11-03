"""检索领域数据结构（#07 · T4.1 关键词通道）。

只放领域层 dataclass：契约字段投影与 HTTP 校验在 ``app/modules_api/contract/knowledge``，
两者分开是为了让服务层可以脱离 FastAPI 单测。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class KeywordQuery:
    """一次关键词检索的入参（``authorization`` 时效已由契约依赖校验）。"""

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

    @property
    def no_evidence(self) -> bool:
        """无证据 = 一条都没召回；有命中但分低不算无证据（阈值由契约方判断）。"""
        return not self.hits
