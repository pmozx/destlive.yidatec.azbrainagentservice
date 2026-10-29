"""重排序客户端：llama.cpp ``--rerank``（Jina / Cohere 同构报文）交叉编码器打分，用于召回后精排。

口径：
1. 输入是「1 个 query + N 个候选文本」，输出是候选的原始下标与相关度分数，**下标必须回指入参**，
   否则调用方拿到的文本与分数会错位；
2. 服务端已加载单个模型时可不传 model；传了会原样下发，便于多模型对比；
3. 重试次数默认 1：精排属在线链路的尾段，宁可快速失败由上层降级，也不要在超时上叠加重试。
"""

from collections.abc import Sequence
from dataclasses import dataclass

from fastapi import status

from app.config.setting import settings
from app.core.exceptions import CustomException

from .base import BaseHttpClient


@dataclass(frozen=True)
class RerankHit:
    """一条精排结果：原候选下标 + 相关度 + 原文（便于直接拼上下文）。"""

    index: int
    score: float
    document: str


class RerankClient(BaseHttpClient):
    """重排序客户端。"""

    service_name = "重排序服务"

    def __init__(
        self,
        base_url: str | None = None,
        *,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
    ) -> None:
        super().__init__(
            settings.RERANK_BASE_URL if base_url is None else base_url,
            timeout=settings.RERANK_TIMEOUT if timeout is None else timeout,
            max_retries=1,
            api_key=settings.RERANK_API_KEY if api_key is None else api_key,
        )
        self.model = model or settings.RERANK_MODEL

    async def rerank(
        self,
        query: str,
        documents: Sequence[str],
        *,
        top_n: int | None = None,
        model: str | None = None,
    ) -> list[RerankHit]:
        """按相关度降序返回候选（top_n 缺省返回全部）。"""
        if not query or not query.strip():
            raise CustomException(msg="重排序 query 不能为空", status_code=status.HTTP_400_BAD_REQUEST)
        candidates = [str(document) for document in documents]
        if not candidates:
            return []
        if top_n is not None and top_n <= 0:
            raise CustomException(msg="top_n 必须大于 0", status_code=status.HTTP_400_BAD_REQUEST)

        payload: dict[str, object] = {"query": query, "documents": candidates}
        if top_n is not None:
            payload["top_n"] = top_n
        model_name = model or self.model
        if model_name:
            payload["model"] = model_name

        data = await self.post_json(settings.RERANK_PATH, payload)
        results = data.get("results") or data.get("data") or []
        hits: list[RerankHit] = []
        for item in results:
            raw_index = item.get("index")
            if raw_index is None or not 0 <= int(raw_index) < len(candidates):
                continue  # 越界下标直接丢弃，避免文本与分数错配
            hits.append(
                RerankHit(
                    index=int(raw_index),
                    score=float(item.get("relevance_score", item.get("score", 0.0)) or 0.0),
                    document=candidates[int(raw_index)],
                )
            )
        hits.sort(key=lambda hit: hit.score, reverse=True)
        return hits if top_n is None else hits[:top_n]
