"""向量化客户端（LangChain 统一入口 ``init_embeddings``）：同一条链路兼容 OpenAI 兼容服务与 Ollama。

约定：
1. **模型实例只从通用入口构造**：``langchain.embeddings.init_embeddings`` 按 ``provider`` 分派
   （openai / azure_openai / cohere / ollama…）；本地部署的 vLLM / TEI / llama.cpp / SGLang
   与 OpenAI 的差别只在地址、不在协议，统一归一成 openai（见 :func:`embedding_provider`）；
2. 调用方只拿 ``list[list[float]]``，顺序与入参一一对应；分批由本层负责，业务侧不用关心 batch；
3. 维度不一致只告警不阻断：本层是通用客户端，真正落库前的强校验在 ``app.core.rag.vector_store``；
4. 空文本直接拒绝——Ollama 对空输入返回 400，静默过滤会让向量与分片错位；
5. 内网/回环目标不读代理环境变量（见 :func:`app.core.clients.base.trust_env_for_url`），
   自建 bge-m3 在局域网里，走代理只会变成 502；
6. 分批**并发**提交（``EMBEDDING_CONCURRENCY`` 控制并发数）并带指数退避重试——
   300 个分片按 batch=16 串行要 19 次往返，同步等完再写下一条纯属浪费；
   并发只影响吞吐，不影响顺序：结果按批次下标回填；
7. 实例应当长期复用（见 :func:`app.core.clients.get_embedding_client`）：
   每次请求 new 一个会把底层 httpx 连接池一起丢掉，既不复用连接也永不关闭。
"""

import asyncio
from collections.abc import Sequence
from typing import Any

import httpx
from fastapi import status
from langchain.embeddings import init_embeddings
from langchain_core.embeddings import Embeddings

from app.config.setting import settings
from app.core.exceptions import CustomException
from app.core.logger import logger

from .base import trust_env_for_url

#: 本地部署的推理服务（vLLM / TEI / llama.cpp / SGLang / Xinference…）都提供 OpenAI 兼容的
#: ``/v1/embeddings``；provider 填这些名字时不必再换客户端，统一归一成 openai。
_OPENAI_COMPATIBLE_PROVIDERS: frozenset[str] = frozenset(
    {
        "local",
        "vllm",
        "sglang",
        "tei",
        "xinference",
        "llamacpp",
        "llama.cpp",
        "lmstudio",
        "openai_compatible",
        "openai-compatible",
    }
)


def embedding_provider(provider: str) -> str | None:
    """``model_provider`` → ``init_embeddings`` 认识的 provider。

    空值返回 None：交给客户端回落到 ``EMBEDDING_PROTOCOL``（保持 env 口径）。
    本地部署常见的 vLLM / TEI / llama.cpp 归一成 openai——它们与 OpenAI 的差别只在地址，
    真正另一种协议只有 Ollama 的 ``/api/embed``。其余名字原样透传，由 ``init_embeddings``
    按 provider 白名单校验（拼错或集成包没装会得到可读的初始化失败）。
    """
    value = (provider or "").strip().lower()
    if not value:
        return None
    return "openai" if value in _OPENAI_COMPATIBLE_PROVIDERS else value


class EmbeddingClient:
    """向量化客户端（模型实例由 ``init_embeddings`` 构造）。"""

    service_name = "向量化服务"

    def __init__(
        self,
        base_url: str | None = None,
        *,
        provider: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        batch_size: int | None = None,
        concurrency: int | None = None,
        max_retries: int | None = None,
    ) -> None:
        self.base_url = (settings.EMBEDDING_BASE_URL if base_url is None else base_url).rstrip("/")
        self.provider = (provider or settings.EMBEDDING_PROTOCOL or "openai").strip().lower()
        self.api_key = settings.EMBEDDING_API_KEY if api_key is None else api_key
        self.model = model or settings.EMBEDDING_MODEL
        self.timeout = settings.EMBEDDING_TIMEOUT if timeout is None else timeout
        self.batch_size = max(1, batch_size or settings.EMBEDDING_BATCH_SIZE)
        self.concurrency = max(1, concurrency or settings.EMBEDDING_CONCURRENCY)
        self.max_retries = max(0, settings.EMBEDDING_MAX_RETRIES if max_retries is None else max_retries)
        self._model_client: Embeddings | None = None
        self._http: httpx.AsyncClient | None = None

    # ── 内部辅助 ──────────────────────────────────────────────────────

    def _init_kwargs(self, trust_env: bool) -> dict[str, Any]:
        """按 provider 准备 ``init_embeddings`` 的构造参数。

        只有 OpenAI 兼容与 Ollama 能吃「本地地址 + 自建连接池」：前者的参数名是
        ``base_url``，后者是 ``client_kwargs``；其余 provider（azure_openai / cohere /
        huggingface…）的地址与密钥口径各不相同，这里不猜，只透传超时，其余交给各自的
        env（如 ``AZURE_OPENAI_ENDPOINT`` / ``COHERE_API_KEY``）。
        """
        if self.provider == "ollama":
            kwargs: dict[str, Any] = {"trust_env": trust_env, "timeout": self.timeout}
            return {
                "base_url": self.base_url,
                "client_kwargs": kwargs,
                "async_client_kwargs": dict(kwargs),
            }
        if self.provider == "openai":
            return {
                # 私有化 OpenAI 兼容服务常不带密钥（非密钥校验，只是必填占位）
                "api_key": self.api_key or "not-needed",
                "base_url": self.base_url,
                "timeout": self.timeout,
                # 不按 tiktoken 切 token：自建服务不保证兼容 tokenizer，整段文本直传更稳
                "check_embedding_ctx_length": False,
                "chunk_size": self.batch_size,
                "http_async_client": self._async_http_client(trust_env),
            }
        return {"timeout": self.timeout}

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
        try:
            client = init_embeddings(self.model, provider=self.provider, **self._init_kwargs(trust_env))
        except (ValueError, ImportError) as exc:
            # provider 不在白名单 / 集成包没装：这是配置问题，不是模型服务故障
            raise CustomException(
                msg=f"{self.service_name} 初始化失败（provider={self.provider}）：{exc}",
                status_code=status.HTTP_400_BAD_REQUEST,
            ) from exc
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

    def _async_http_client(self, trust_env: bool) -> httpx.AsyncClient:
        """自己的 httpx 客户端：连接池可复用、关停时能显式关闭（内网服务必须直连）。"""
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=self.timeout, trust_env=trust_env)
        return self._http

    async def _embed_batch(
        self, client: Embeddings, batch: list[str], semaphore: asyncio.Semaphore
    ) -> list[list[float]]:
        """单批向量化：并发受信号量约束，失败按指数退避重试。"""
        last_exc: Exception | None = None
        async with semaphore:
            for attempt in range(self.max_retries + 1):
                try:
                    batch_vectors = await client.aembed_documents(batch)
                except Exception as exc:  # noqa: BLE001 客户端异常统一收敛成可读的 502
                    last_exc = exc
                    if attempt < self.max_retries:
                        # 0.5s / 1s / 2s…：模型服务偶发 502/超时时，重试比整单失败便宜得多
                        await asyncio.sleep(min(2**attempt, 8) * 0.5)
                        continue
                    raise CustomException(
                        msg=(
                            f"{self.service_name} 调用失败（已重试 {self.max_retries} 次）："
                            f"{type(exc).__name__}: {str(exc)[:200]}"
                        ),
                        status_code=status.HTTP_502_BAD_GATEWAY,
                    ) from exc
                if len(batch_vectors) != len(batch):
                    raise CustomException(
                        msg=f"向量化返回条数与请求不一致：请求 {len(batch)}，返回 {len(batch_vectors)}",
                        status_code=status.HTTP_502_BAD_GATEWAY,
                    )
                return [[float(value) for value in vector] for vector in batch_vectors]
        # 理论不可达：循环内要么 return 要么 raise
        raise CustomException(msg=f"{self.service_name} 调用失败：{last_exc!r}", status_code=status.HTTP_502_BAD_GATEWAY)

    async def embed_texts(self, texts: Sequence[str], *, batch_size: int | None = None) -> list[list[float]]:
        """批量向量化，返回顺序与入参一致（分批并发提交，结果按批次顺序回填）。"""
        items = [str(text) for text in texts]
        if not items:
            return []
        if any(not item.strip() for item in items):
            raise CustomException(msg="存在空文本，向量化前请先过滤空白分片", status_code=status.HTTP_400_BAD_REQUEST)

        client = self._client()
        size = max(1, batch_size or self.batch_size)
        batches = [items[start : start + size] for start in range(0, len(items), size)]
        semaphore = asyncio.Semaphore(self.concurrency)
        # gather 保序：结果下标与 batches 一一对应，所以并发不会打乱“向量 ↔ 分片”的对应关系
        results = await asyncio.gather(*(self._embed_batch(client, batch, semaphore) for batch in batches))
        vectors = [vector for batch_vectors in results for vector in batch_vectors]

        self._warn_dimension(vectors)
        return vectors

    async def embed_query(self, text: str) -> list[float]:
        """单条向量化（检索时对 query 用同一模型，勿混用不同模型）。"""
        vectors = await self.embed_texts([text])
        return vectors[0]

    async def aclose(self) -> None:
        """关闭自有 httpx 连接池（应用关停时由 :func:`app.core.clients.close_clients` 统一调用）。"""
        if self._http is not None:
            await self._http.aclose()
            self._http = None
        self._model_client = None
