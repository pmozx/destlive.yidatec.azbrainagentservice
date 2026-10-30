"""向量化客户端（LangChain Embeddings）：同一入口兼容 OpenAI（``/v1/embeddings``）与 Ollama（``/api/embed``）。

约定：
1. 调用方只拿 ``list[list[float]]``，顺序与入参一一对应；分批由本层负责，业务侧不用关心 batch；
2. 维度不一致只告警不阻断：本层是通用客户端，真正落库前的强校验在 ``app.core.rag.vector_store``；
3. 空文本直接拒绝——Ollama 对空输入返回 400，静默过滤会让向量与分片错位；
4. 内网/回环目标不读代理环境变量（见 :func:`app.core.clients.base.trust_env_for_url`），
   自建 bge-m3 在局域网里，走代理只会变成 502。
"""

from collections.abc import Sequence

import httpx
from fastapi import status
from langchain_core.embeddings import Embeddings
from langchain_ollama import OllamaEmbeddings
from langchain_openai import OpenAIEmbeddings

from app.config.setting import settings
from app.core.exceptions import CustomException
from app.core.logger import logger

from .base import trust_env_for_url

_SUPPORTED_PROTOCOLS: tuple[str, ...] = ("openai", "ollama")


class EmbeddingClient:
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
        self.base_url = (settings.EMBEDDING_BASE_URL if base_url is None else base_url).rstrip("/")
        self.protocol = (protocol or settings.EMBEDDING_PROTOCOL or "openai").strip().lower()
        if self.protocol not in _SUPPORTED_PROTOCOLS:
            raise CustomException(
                msg=f"不支持的 EMBEDDING_PROTOCOL={self.protocol}，可选：{'/'.join(_SUPPORTED_PROTOCOLS)}",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        self.api_key = settings.EMBEDDING_API_KEY if api_key is None else api_key
        self.model = model or settings.EMBEDDING_MODEL
        self.timeout = settings.EMBEDDING_TIMEOUT if timeout is None else timeout
        self.batch_size = max(1, batch_size or settings.EMBEDDING_BATCH_SIZE)
        self._model_client: Embeddings | None = None

    # ── 内部辅助 ──────────────────────────────────────────────────────

    def _client(self) -> Embeddings:
        """惰性构造 LangChain Embeddings（地址未配置时按调用失败处理，而不是启动即崩）。"""
        if self._model_client is not None:
            return self._model_client
        if not self.base_url:
            raise CustomException(
                msg=f"{self.service_name} 地址未配置，请先在 backend/env/.env.<env> 填写 EMBEDDING_BASE_URL",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        if not self.model:
            raise CustomException(
                msg="向量化模型名称未配置（EMBEDDING_MODEL）",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        trust_env = trust_env_for_url(self.base_url)
        if self.protocol == "ollama":
            client: Embeddings = OllamaEmbeddings(
                model=self.model,
                base_url=self.base_url,
                client_kwargs={"trust_env": trust_env, "timeout": self.timeout},
                async_client_kwargs={"trust_env": trust_env, "timeout": self.timeout},
            )
        else:
            client = OpenAIEmbeddings(
                model=self.model,
                # 私有化 OpenAI 兼容服务常不带密钥（非密钥校验，只是必填占位）
                api_key=self.api_key or "not-needed",
                base_url=self.base_url,
                timeout=self.timeout,
                # 不按 tiktoken 切 token：自建服务不保证兼容 tokenizer，整段文本直传更稳
                check_embedding_ctx_length=False,
                chunk_size=self.batch_size,
                http_async_client=_async_http_client(trust_env, self.timeout),
            )
        self._model_client = client
        return client

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

        client = self._client()
        size = max(1, batch_size or self.batch_size)
        vectors: list[list[float]] = []
        for start in range(0, len(items), size):
            batch = items[start : start + size]
            try:
                batch_vectors = await client.aembed_documents(batch)
            except Exception as exc:  # noqa: BLE001 客户端异常统一成可读的 502
                raise CustomException(
                    msg=f"{self.service_name} 调用失败：{type(exc).__name__}: {str(exc)[:200]}",
                    status_code=status.HTTP_502_BAD_GATEWAY,
                ) from exc
            if len(batch_vectors) != len(batch):
                raise CustomException(
                    msg=f"向量化返回条数与请求不一致：请求 {len(batch)}，返回 {len(batch_vectors)}",
                    status_code=status.HTTP_502_BAD_GATEWAY,
                )
            vectors.extend([[float(value) for value in vector] for vector in batch_vectors])

        self._warn_dimension(vectors)
        return vectors

    async def embed_query(self, text: str) -> list[float]:
        """单条向量化（检索时对 query 用同一模型，勿混用不同模型）。"""
        vectors = await self.embed_texts([text])
        return vectors[0]


def _async_http_client(trust_env: bool, timeout: float) -> httpx.AsyncClient:
    """按目标地址决定是否读取代理环境变量（内网服务必须直连）。"""
    return httpx.AsyncClient(timeout=timeout, trust_env=trust_env)
