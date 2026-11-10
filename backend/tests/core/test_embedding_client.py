"""向量化客户端（``app.core.clients.embedding``）的分批并发、重试与初始化回归测试。

分批并发与重试是报告 P1-7 的修复点：串行分批把「一次写入」的耗时放大成 N 次往返，
而任何一批抖动都会让整单失败且没有重试。测试用桩替换底层 LangChain Embeddings，
不触达真实模型服务（sqlite/pg 都不需要）。
初始化部分只验证 ``init_embeddings`` 的入参分派（provider 归一、kwargs 形状），不联网。
"""

import asyncio
from typing import Any

import pytest
from fastapi import status

from app.core.clients import embedding as embedding_module
from app.core.clients.embedding import EmbeddingClient, embedding_provider
from app.core.exceptions import CustomException

DIM = 8  # 用小维度：本用例只关心批次与顺序，维度告警由 vector_store 负责


class _StubEmbeddings:
    """桩：记录每次批量大小与并发峰值，可注入失败次数。"""

    def __init__(self, *, fail_times: int = 0, delay: float = 0.01) -> None:
        self.fail_times = fail_times
        self.delay = delay
        self.batch_sizes: list[int] = []
        self.in_flight = 0
        self.peak = 0

    async def aembed_documents(self, batch: list[str]) -> list[list[float]]:
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            await asyncio.sleep(self.delay)
            self.batch_sizes.append(len(batch))
            if self.fail_times > 0:
                self.fail_times -= 1
                raise RuntimeError("模型服务 502")
            # 向量内容编码“文本序号”，便于断言结果顺序没被打乱
            return [[float(text.rsplit("-", 1)[-1])] * DIM for text in batch]
        finally:
            self.in_flight -= 1


def _client(stub: _StubEmbeddings, **kwargs) -> EmbeddingClient:
    client = EmbeddingClient(base_url="http://127.0.0.1:9", provider="openai", **kwargs)
    client._model_client = stub  # 绕过真实客户端构造（不读 settings 的地址与密钥）
    return client


async def test_batches_run_concurrently_and_keep_order() -> None:
    """10 条按 batch=2 切 5 批：并发峰值 >1，且结果顺序与入参严格一致。"""
    stub = _StubEmbeddings()
    client = _client(stub, batch_size=2, concurrency=5, max_retries=0)

    texts = [f"text-{i}" for i in range(10)]
    vectors = await client.embed_texts(texts)

    assert len(vectors) == 10
    # 顺序：第 i 条向量编码的就是 i（并发回填后仍按下标排列）
    assert [int(vector[0]) for vector in vectors] == list(range(10))
    assert sorted(stub.batch_sizes) == [2, 2, 2, 2, 2]
    assert stub.peak > 1, "分批没有并发，P1-7 的串行问题回归了"


async def test_concurrency_is_bounded_by_semaphore() -> None:
    """并发数受 EMBEDDING_CONCURRENCY 约束：不因为分片多就无限打模型服务。"""
    stub = _StubEmbeddings()
    client = _client(stub, batch_size=1, concurrency=2, max_retries=0)

    await client.embed_texts([f"t-{i}" for i in range(8)])

    assert stub.peak <= 2


async def test_failed_batch_is_retried() -> None:
    """前两批各失败一次：重试后仍应拿到完整结果（不是整单失败）。"""
    stub = _StubEmbeddings(fail_times=2)
    client = _client(stub, batch_size=2, concurrency=1, max_retries=2)

    vectors = await client.embed_texts([f"text-{i}" for i in range(4)])

    assert len(vectors) == 4
    assert stub.fail_times == 0


async def test_retry_exhausted_raises_502() -> None:
    """重试次数用尽仍失败：归一为 502（可重试语义），信息里带重试次数。"""
    stub = _StubEmbeddings(fail_times=99)
    client = _client(stub, batch_size=2, concurrency=1, max_retries=1)

    with pytest.raises(CustomException) as excinfo:
        await client.embed_texts(["a-1", "a-2"])

    assert excinfo.value.status_code == status.HTTP_502_BAD_GATEWAY
    assert "已重试 1 次" in excinfo.value.msg


async def test_empty_text_is_rejected_before_calling_model() -> None:
    """空文本直接 400：静默过滤会让向量与分片错位。"""
    stub = _StubEmbeddings()
    client = _client(stub, batch_size=2, concurrency=1, max_retries=0)

    with pytest.raises(CustomException) as excinfo:
        await client.embed_texts(["ok", "   "])

    assert excinfo.value.status_code == status.HTTP_400_BAD_REQUEST
    assert stub.batch_sizes == []


# ── 初始化：init_embeddings 的入参分派 ────────────────────────────────


def test_local_provider_is_normalized_to_openai() -> None:
    """本地部署的 provider 名归一到 openai：它们与 OpenAI 的差别只在地址。"""
    assert embedding_provider("vllm") == "openai"
    assert embedding_provider("TEI") == "openai"
    assert embedding_provider("llama.cpp") == "openai"
    assert embedding_provider("ollama") == "ollama"
    assert embedding_provider("cohere") == "cohere"
    assert embedding_provider("") is None, "空值要交给客户端回落 EMBEDDING_PROTOCOL"


async def test_openai_provider_initializes_via_init_embeddings(monkeypatch: pytest.MonkeyPatch) -> None:
    """openai 分支：地址 / 密钥 / 批大小与自建连接池都交给 init_embeddings。"""
    calls: dict[str, Any] = {}

    def fake_init(model: str, *, provider: str | None = None, **kwargs: Any) -> _StubEmbeddings:
        calls.update({"model": model, "provider": provider, "kwargs": kwargs})
        return _StubEmbeddings()

    monkeypatch.setattr(embedding_module, "init_embeddings", fake_init)
    client = EmbeddingClient(
        base_url="http://10.0.0.9:8080/v1",
        provider="openai",
        api_key="",
        model="bge-m3",
        timeout=7,
        batch_size=4,
    )
    try:
        client._client()
    finally:
        await client.aclose()

    assert calls["model"] == "bge-m3"
    assert calls["provider"] == "openai"
    kwargs = calls["kwargs"]
    assert kwargs["base_url"] == "http://10.0.0.9:8080/v1"
    assert kwargs["api_key"] == "not-needed", "私有化服务无密钥时用占位，不能传空串"
    assert kwargs["check_embedding_ctx_length"] is False
    assert kwargs["chunk_size"] == 4


def test_ollama_provider_passes_client_kwargs(monkeypatch: pytest.MonkeyPatch) -> None:
    """ollama 分支：地址走 base_url，超时/直连走 client_kwargs（不是 OpenAI 那套参数名）。"""
    calls: dict[str, Any] = {}

    def fake_init(model: str, *, provider: str | None = None, **kwargs: Any) -> _StubEmbeddings:
        calls.update({"model": model, "provider": provider, "kwargs": kwargs})
        return _StubEmbeddings()

    monkeypatch.setattr(embedding_module, "init_embeddings", fake_init)
    client = EmbeddingClient(
        base_url="http://10.0.0.9:11434",
        provider="ollama",
        model="bge-m3:latest",
        timeout=9,
    )

    client._client()

    assert calls["provider"] == "ollama"
    assert calls["model"] == "bge-m3:latest", "ollama 的 tag 带冒号，provider 显式传入时不得被当作前缀解析"
    kwargs = calls["kwargs"]
    assert kwargs["base_url"] == "http://10.0.0.9:11434"
    assert kwargs["client_kwargs"]["timeout"] == 9
    assert kwargs["async_client_kwargs"]["timeout"] == 9


def test_unknown_provider_raises_readable_400(monkeypatch: pytest.MonkeyPatch) -> None:
    """provider 拼错或集成包没装：归一为 400 且带 provider 名，不是 500 或静默降级。"""

    def boom(*args: Any, **kwargs: Any) -> None:
        raise ValueError("Provider 'foo' is not supported.")

    monkeypatch.setattr(embedding_module, "init_embeddings", boom)
    client = EmbeddingClient(base_url="http://127.0.0.1:9/v1", provider="foo", model="m")

    with pytest.raises(CustomException) as excinfo:
        client._client()

    assert excinfo.value.status_code == status.HTTP_400_BAD_REQUEST
    assert "provider=foo" in excinfo.value.msg
