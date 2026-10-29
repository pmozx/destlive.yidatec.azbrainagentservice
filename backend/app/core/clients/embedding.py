"""向量化客户端：同一入口兼容 OpenAI（``/v1/embeddings``）与 Ollama（``/api/embed``）两种私有化部署。

约定：
1. 调用方只拿 ``list[list[float]]``，顺序与入参一一对应；分批由本层负责，业务侧不用关心 batch；
2. 维度不一致只告警不阻断：本层是通用客户端，真正落库前的强校验在 ``integration_kb.chunk.vector_store``；
3. 空文本直接拒绝——Ollama 对空输入返回 400，静默过滤会让向量与分片错位。
"""

from collections.abc import Sequence

from fastapi import status

from app.config.setting import settings
from app.core.exceptions import CustomException
from app.core.logger import logger

from .base import BaseHttpClient

_SUPPORTED_PROTOCOLS: tuple[str, ...] = ("openai", "ollama")


class EmbeddingClient(BaseHttpClient):
    """向量化客户端（协议由 EMBEDDING_PROTOCOL 决定）。"""

    service_name = "向量化服务"

    def __init__(
        self,
        base_url: str | None = None,
        *,
        protocol: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        batch_size: int | None = None,
    ) -> None:
        super().__init__(
            settings.EMBEDDING_BASE_URL if base_url is None else base_url,
            timeout=settings.EMBEDDING_TIMEOUT if timeout is None else timeout,
            max_retries=1,
            api_key=settings.EMBEDDING_API_KEY if api_key is None else api_key,
        )
        self.protocol = (protocol or settings.EMBEDDING_PROTOCOL or "openai").strip().lower()
        if self.protocol not in _SUPPORTED_PROTOCOLS:
            raise CustomException(
                msg=f"不支持的 EMBEDDING_PROTOCOL={self.protocol}，可选：{'/'.join(_SUPPORTED_PROTOCOLS)}",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        self.model = model or settings.EMBEDDING_MODEL
        self.batch_size = max(1, batch_size or settings.EMBEDDING_BATCH_SIZE)

    # ── 内部辅助 ──────────────────────────────────────────────────────

    def _path(self) -> str:
        # openai 的 base_url 由使用者带到 /v1；ollama 的 base_url 到端口
        return "embeddings" if self.protocol == "openai" else "api/embed"

    def _payload(self, batch: Sequence[str]) -> dict[str, object]:
        if not self.model:
            raise CustomException(
                msg="向量化模型名称未配置（EMBEDDING_MODEL）",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return {"model": self.model, "input": list(batch)}

    def _parse(self, data: dict, *, expected: int) -> list[list[float]]:
        if self.protocol == "ollama":
            vectors = data.get("embeddings") or []
        else:
            items = sorted(data.get("data") or [], key=lambda item: item.get("index", 0))
            vectors = [item.get("embedding") or [] for item in items]
        if len(vectors) != expected:
            raise CustomException(
                msg=f"向量化返回条数与请求不一致：请求 {expected}，返回 {len(vectors)}",
                status_code=status.HTTP_502_BAD_GATEWAY,
            )
        return [[float(value) for value in vector] for vector in vectors]

    def _warn_dimension(self, vectors: Sequence[Sequence[float]]) -> None:
        dims = {len(vector) for vector in vectors}
        if len(dims) != 1:
            raise CustomException(
                msg=f"同一批次向量维度不一致：{sorted(dims)}",
                status_code=status.HTTP_502_BAD_GATEWAY,
            )
        dim = dims.pop()
        if dim != settings.VECTOR_DIM:
            logger.warning(
                "向量维度 {} 与 VECTOR_DIM={} 不一致，写入 pgvector 前会被拒绝（请核对 EMBEDDING_MODEL）",
                dim,
                settings.VECTOR_DIM,
            )

    # ── 对外能力 ──────────────────────────────────────────────────────

    async def embed_texts(self, texts: Sequence[str], *, batch_size: int | None = None) -> list[list[float]]:
        """批量向量化，返回顺序与入参一致。"""
        items = [str(text) for text in texts]
        if not items:
            return []
        if any(not item.strip() for item in items):
            raise CustomException(msg="存在空文本，向量化前请先过滤空白分片", status_code=status.HTTP_400_BAD_REQUEST)

        size = max(1, batch_size or self.batch_size)
        vectors: list[list[float]] = []
        for start in range(0, len(items), size):
            batch = items[start : start + size]
            data = await self.post_json(self._path(), self._payload(batch))
            vectors.extend(self._parse(data, expected=len(batch)))

        self._warn_dimension(vectors)
        return vectors

    async def embed_query(self, text: str) -> list[float]:
        """单条向量化（检索时对 query 用同一模型，勿混用不同模型）。"""
        vectors = await self.embed_texts([text])
        return vectors[0]
