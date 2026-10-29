"""聊天模型客户端（OpenAI 兼容协议）：DeepSeek 直连、自建 vLLM、Azure 网关共用同一套请求体。

设计取舍：
1. 不套 langchain 的 ChatModel：集成期要能直接看到请求地址、响应原文与耗时，裸 HTTP 更好排障；
   langchain/langgraph 编排留给业务模块按需包装本客户端，避免两头都改提示词；
2. 只暴露 ``chat``（一次性返回）与 ``stream_chat``（SSE 增量）两个入口，
   提示词模板、工具调用、结果解析都不在这一层。
"""

import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Any, TypedDict

import httpx
from fastapi import status

from app.config.setting import settings
from app.core.exceptions import CustomException

from .base import BaseHttpClient, trust_env_for_url


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


class LlmClient(BaseHttpClient):
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
        super().__init__(
            settings.LLM_BASE_URL if base_url is None else base_url,
            timeout=settings.LLM_TIMEOUT if timeout is None else timeout,
            max_retries=settings.LLM_MAX_RETRIES if max_retries is None else max_retries,
            api_key=settings.LLM_API_KEY if api_key is None else api_key,
        )
        self.model = model or settings.LLM_MODEL

    def _payload(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float,
        max_tokens: int | None,
        model: str | None,
        stream: bool,
    ) -> dict[str, Any]:
        model_name = model or self.model
        if not model_name:
            raise CustomException(
                msg="LLM 模型名称未配置（LLM_MODEL）",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        payload: dict[str, Any] = {
            "model": model_name,
            "messages": [dict(message) for message in messages],
            "temperature": temperature,
            "stream": stream,
        }
        if stream:
            # 用量统计依赖末帧的 usage 对象；不支持的网关会忽略该字段，不影响正文流
            payload["stream_options"] = {"include_usage": True}
        if max_tokens:
            payload["max_tokens"] = max_tokens
        return payload

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        model: str | None = None,
    ) -> str:
        """一次性拿到完整回答（离线批量、评测脚本用）。"""
        payload = self._payload(messages, temperature=temperature, max_tokens=max_tokens, model=model, stream=False)
        data = await self.post_json("chat/completions", payload)
        choices = data.get("choices") or []
        content = ((choices[0] or {}).get("message") or {}).get("content") if choices else None
        if not content:
            raise CustomException(
                msg=f"{self.service_name} 返回空内容：{str(data)[:200]}",
                status_code=status.HTTP_502_BAD_GATEWAY,
            )
        return str(content)

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
        self._require_base_url()
        payload = self._payload(messages, temperature=temperature, max_tokens=max_tokens, model=model, stream=True)
        url = self._url("chat/completions")

        async with httpx.AsyncClient(timeout=self.timeout, trust_env=trust_env_for_url(url)) as client:
            async with client.stream("POST", url, json=payload, headers=self._headers()) as response:
                if response.status_code >= 400:
                    body = await response.aread()
                    raise CustomException(
                        msg=f"{self.service_name} 流式调用失败（HTTP {response.status_code}）：{body[:200]!r}",
                        status_code=status.HTTP_502_BAD_GATEWAY,
                    )
                async for line in response.aiter_lines():
                    data = line.strip()
                    if not data.startswith("data:"):
                        continue  # 注释行/心跳
                    data = data[5:].strip()
                    if not data:
                        continue
                    if data == "[DONE]":
                        break
                    try:
                        event = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    usage = event.get("usage") or {}
                    choices = event.get("choices") or []
                    content = ((choices[0] or {}).get("delta") or {}).get("content") if choices else None
                    chunk = ChatStreamChunk(
                        delta=str(content) if content else "",
                        tokens_in=int(usage.get("prompt_tokens") or 0),
                        tokens_out=int(usage.get("completion_tokens") or 0),
                    )
                    if chunk.delta or chunk.tokens_in or chunk.tokens_out:
                        yield chunk
