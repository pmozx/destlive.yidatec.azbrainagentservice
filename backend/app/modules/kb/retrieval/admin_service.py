"""知识检索管理端服务：检索调试台与通道配置投影。

领域语义（授权先于召回、双通道、RRF、精排、``mode`` 分发）全在 ``service.py``；本层只补管理端
需要、而契约链路不需要的三件事：

1. **计时与出参投影**：页面要看真实耗时，而不是去翻日志；
2. **领域异常 → HTTP 503**：授权数据 / 向量化不可用都如实报错，不用空 ``hits`` 冒充
   「库里没有依据」——那会把排障引到错的方向；
3. **检索参数只读投影**：``settings`` 是运行时唯一来源，改配置走环境变量并重启，
   页面上不提供写入口，避免「页面改完实际不生效」的假象。
"""

from time import perf_counter

from fastapi import status

from app.config.setting import settings
from app.core.exceptions import CustomException

from .schema import (
    KbRetrievalConfigOutSchema,
    KbRetrievalHitOutSchema,
    KbRetrievalSearchOutSchema,
    KbRetrievalSearchSchema,
    SearchQuery,
)
from .service import KbRetrievalService, RetrievalAuthorizationUnavailable, RetrievalEmbeddingUnavailable


class KbRetrievalAdminService:
    """知识检索管理端服务（无请求级会话：检索链路自己开只读连接）。"""

    def __init__(self) -> None:
        self.retrieval = KbRetrievalService()

    def config(self) -> KbRetrievalConfigOutSchema:
        """当前生效的检索参数；两个 ``*_ready`` 直接决定自然语言通道能不能用。"""
        return KbRetrievalConfigOutSchema(
            embedding_model=settings.EMBEDDING_MODEL,
            embedding_ready=bool(settings.EMBEDDING_BASE_URL),
            vector_dim=settings.VECTOR_DIM,
            rerank_model=settings.RERANK_MODEL,
            rerank_ready=bool(settings.RERANK_BASE_URL),
            keyword_candidate_max=settings.SEARCH_KEYWORD_CANDIDATE_MAX,
            vector_candidate_max=settings.SEARCH_VECTOR_CANDIDATE_MAX,
            vector_top_k=settings.KB_RETRIEVE_TOP_K,
            vector_min_score=settings.KB_RETRIEVE_MIN_SCORE,
            rerank_window=settings.SEARCH_RERANK_WINDOW,
            rerank_doc_chars=settings.SEARCH_RERANK_DOC_CHARS,
            rerank_timeout=settings.SEARCH_RERANK_TIMEOUT,
            rerank_min_score=settings.KB_RERANK_MIN_SCORE,
            rerank_relative_ratio=settings.KB_RERANK_RELATIVE_RATIO,
            rerank_score_is_logit=settings.RERANK_SCORE_IS_LOGIT,
            rrf_k=settings.SEARCH_RRF_K,
        )

    async def search(self, data: KbRetrievalSearchSchema) -> KbRetrievalSearchOutSchema:
        """执行一次检索：两条通道共用一个出口，耗时含向量化与精排。"""
        started = perf_counter()
        try:
            # ``mode`` 分发在领域层（与契约 #07 同一入口），本层只负责计时与异常翻译
            result = await self.retrieval.search(
                data.mode,
                SearchQuery(
                    query=data.query,
                    tenant_id=data.tenant_id,
                    user_id=data.user_id,
                    limit=data.limit,
                ),
            )
        except (RetrievalAuthorizationUnavailable, RetrievalEmbeddingUnavailable) as exc:
            raise CustomException(msg=str(exc), status_code=status.HTTP_503_SERVICE_UNAVAILABLE) from exc
        return KbRetrievalSearchOutSchema(
            mode=data.mode,
            query=data.query,
            returned_count=len(result.hits),
            no_evidence=result.no_evidence,
            elapsed_ms=round((perf_counter() - started) * 1000),
            authorized_publication_count=result.authorized_publication_count,
            hits=[KbRetrievalHitOutSchema.model_validate(hit) for hit in result.hits],
        )
