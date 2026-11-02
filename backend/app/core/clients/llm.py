"""聊天模型客户端（LangChain ``ChatOpenAI``）：DeepSeek 直连、自建 vLLM、Azure 网关共用同一套请求体。

设计取舍：
1. 用 ``ChatOpenAI`` 而不是裸 httpx：SSE 解析、增量合并、用量字段、重试都由 langchain-openai 维护，
   本层只保留业务口径（消息体、超时、内网直连、错误归一）；
2. 只暴露 ``chat``（一次性返回）与 ``stream_chat`` / ``stream_chat_events``（SSE 增量），
   提示词模板、工具调用、结果解析都不在这一层；
3. 内网/回环目标不读代理环境变量：自建的 vLLM 在局域网里，一旦被 ``HTTP_PROXY`` 劫持
   就只剩 502（见 :func:`app.core.clients.base.trust_env_for_url`），因此显式注入 httpx 客户端；
4. 用量统计依赖 ``stream_usage=True``：网关支持时末帧带 usage，映射到 ``ChatStreamChunk`` 的
   ``tokens_in`` / ``tokens_out``；不支持的网关保持 0，不影响正文流。
"""

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Any, TypedDict

import httpx
from fastapi import status
from langchain_core.messages import AIMessageChunk
from langchain_openai import ChatOpenAI

from app.config.setting import settings
from app.core.exceptions import CustomException

from .base import trust_env_for_url


class ChatMessage(TypedDict):
    """OpenAI 兼容消息体（role 取 system / user / assistant）。"""

    role: str
    content: str


@dataclass(frozen=True, slots=True)
class ChatStreamChunk:
    """一条流式分片：增量正文 + （可能为 0 的）累计用量。

    用量只在流末尾的那一片里给全（OpenAI 兼容服务在 ``stream_options.include_usage``
    打开时的行为），因此调用方应「有值就覆盖、无值就沿用」。
    """

    delta: str = ""
    tokens_in: int = 0
    tokens_out: int = 0


def _message_text(content: Any) -> str:
    """把消息内容归一为纯文本（LangChain 1.x 可能给内容块列表）。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "".join(parts)
    return "" if content is None else str(content)


class LlmClient:
    """聊天模型客户端；参数缺省时取环境配置，显式传参优先（便于多模型对比调试）。"""

    service_name = "LLM 服务"

    def __init__(
        self,
        base_url: str | None = None,
        *,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
    ) -> None:
        self.base_url = (settings.LLM_BASE_URL if base_url is None else base_url).rstrip("/")
        self.api_key = settings.LLM_API_KEY if api_key is None else api_key
        self.model = model or settings.LLM_MODEL
        self.timeout = settings.LLM_TIMEOUT if timeout is None else timeout
        self.max_retries = max(0, settings.LLM_MAX_RETRIES if max_retries is None else max_retries)
        # 模型名随调用可变（多模型对比）：按模型名缓存已构造的 ChatOpenAI 实例
        self._clients: dict[str, ChatOpenAI] = {}
        # 每个 ChatOpenAI 自带的 httpx 客户端，关停时统一关闭
        self._http_clients: list[httpx.AsyncClient] = []

    # ── 内部辅助 ──────────────────────────────────────────────────────

    def _client(self, model: str | None) -> ChatOpenAI:
        model_name = model or self.model
        if not self.base_url:
            raise CustomException(
                msg=f"{self.service_name} 地址未配置，请先在 backend/env/.env.<env> 填写 LLM_BASE_URL",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        if not model_name:
            raise CustomException(
                msg="LLM 模型名称未配置（LLM_MODEL）",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        cached = self._clients.get(model_name)
        if cached is not None:
            return cached
        trust_env = trust_env_for_url(self.base_url)
        http_client = httpx.AsyncClient(timeout=self.timeout, trust_env=trust_env)
        self._http_clients.append(http_client)
        client = ChatOpenAI(
            model=model_name,
            # 私有化 vLLM 常不带密钥，而 OpenAI SDK 不允许空 key（不是密钥校验，只是必填占位）
            api_key=self.api_key or "not-needed",
            base_url=self.base_url,
            timeout=self.timeout,
            max_retries=self.max_retries,
            streaming=True,
            # 末帧带 usage（映射到 usage_metadata），落库 token 统计依赖它
            stream_usage=True,
            http_async_client=http_client,
        )
        self._clients[model_name] = client
        return client

    async def aclose(self) -> None:
        """关闭所有模型实例底层的 httpx 连接池（应用关停时统一调用）。"""
        for http_client in self._http_clients:
            await http_client.aclose()
        self._http_clients.clear()
        self._clients.clear()

    def messages_for(self, messages: Sequence[ChatMessage]) -> list[tuple[str, str]]:
        """把 OpenAI 兼容消息体转成 LangChain 入参形式（保持顺序与角色）。"""
        return [(str(message["role"]), str(message["content"])) for message in messages]

    # ── 对外能力 ──────────────────────────────────────────────────────

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        model: str | None = None,
    ) -> str:
        """一次性拿到完整回答（离线批量、评测脚本用）。"""
        try:
            message = await self._client(model).ainvoke(
                self.messages_for(messages),
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except Exception as exc:  # noqa: BLE001 客户端异常统一成可读的 502
            raise self._failure(exc) from exc
        content = _message_text(message.content).strip()
        if not content:
            raise CustomException(
                msg=f"{self.service_name} 返回空内容",
                status_code=status.HTTP_502_BAD_GATEWAY,
            )
        return content

    async def stream_chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        model: str | None = None,
    ) -> AsyncIterator[str]:
        """按 SSE 增量产出回答片段（前端打字机效果 / 长回答降首字节延迟）。"""
        async for chunk in self.stream_chat_events(
            messages, temperature=temperature, max_tokens=max_tokens, model=model
        ):
            if chunk.delta:
                yield chunk.delta

    async def stream_chat_events(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        model: str | None = None,
    ) -> AsyncIterator[ChatStreamChunk]:
        """流式产出「增量正文 + 用量」：需要落库 token 统计的调用方用这个入口。"""
        try:
            stream = self._client(model).astream(
                self.messages_for(messages),
                temperature=temperature,
                max_tokens=max_tokens,
            )
            async for chunk in stream:
                yield _to_stream_chunk(chunk)
        except CustomException:
            raise
        except Exception as exc:  # noqa: BLE001 流式故障统一成可读的 502
            raise self._failure(exc) from exc

    def _failure(self, exc: Exception) -> CustomException:
        """把 SDK 异常归一成可读的 502（保留原文前 200 字，便于排障）。"""
        return CustomException(
            msg=f"{self.service_name} 调用失败：{type(exc).__name__}: {str(exc)[:200]}",
            status_code=status.HTTP_502_BAD_GATEWAY,
        )


def _to_stream_chunk(chunk: AIMessageChunk) -> ChatStreamChunk:
    """LangChain 增量块 → 业务分片（正文 + 用量）。"""
    usage = getattr(chunk, "usage_metadata", None) or {}
    return ChatStreamChunk(
        delta=_message_text(chunk.content),
        tokens_in=int(usage.get("input_tokens") or 0),
        tokens_out=int(usage.get("output_tokens") or 0),
    )


__all__ = ["ChatMessage", "ChatStreamChunk", "LlmClient"]
