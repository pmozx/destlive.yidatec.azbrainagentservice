"""检索环节：问题向量化 + 知识库召回（chat 侧唯一的知识库读入口）。

只做两件事：把问题变成向量、把向量交给 ``integration_kb.retrieval``。
不在这里 join 文档表、不在这里判断上架状态——过滤口径属于知识库域。
"""

from collections.abc import Sequence

from fastapi import status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.setting import settings
from app.core.clients import EmbeddingClient
from app.core.exceptions import CustomException
from app.core.logger import logger
from app.modules.integration_kb.retrieval import KnowledgeHit, search_knowledge


async def retrieve(
    db: AsyncSession,
    query: str,
    *,
    space_ids: Sequence[int] | None = None,
    top_k: int | None = None,
    min_score: float | None = None,
) -> list[KnowledgeHit]:
    """按问题文本召回知识命中（向量召回 → 上架/解析完成过滤 → 得分降序）。

    异常口径：向量化或检索失败一律抛 ``CustomException``，由链路层转成
    ``retrievalUnavailable`` 错误帧——「服务不可用」绝不能伪装成「库里没有」。
    """
    text = (query or "").strip()
    if not text:
        raise CustomException(msg="检索问题不能为空", status_code=status.HTTP_400_BAD_REQUEST)
    try:
        vector = await EmbeddingClient().embed_query(text)
    except CustomException:
        raise
    except Exception as exc:  # noqa: BLE001 客户端内部异常统一为可读错误
        raise CustomException(
            msg=f"问题向量化失败：{exc}",
            status_code=status.HTTP_502_BAD_GATEWAY,
        ) from exc

    hits = await search_knowledge(
        db,
        vector,
        space_ids=list(space_ids) if space_ids else None,
        top_k=top_k or settings.KB_RETRIEVE_TOP_K,
        min_score=settings.KB_RETRIEVE_MIN_SCORE if min_score is None else min_score,
    )
    logger.info(
        f"知识召回完成：space={len(space_ids) if space_ids else '全部'} 候选={len(hits)} "
        f"top_k={top_k or settings.KB_RETRIEVE_TOP_K}"
    )
    return hits
