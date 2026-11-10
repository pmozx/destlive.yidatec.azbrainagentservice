"""聊天模型客户端：DeepSeek 直连、自建 vLLM、Azure 网关共用同一套请求体。

设计取舍：
1. **厂商类只出现在本文件**：模型由 ``init_chat_model`` 统一构造，业务侧只认 ``LlmClient``
   与 LangChain 标准消息；换 provider 改这里 + 依赖即可（规范 L22）。用框架客户端而不是裸
   httpx，是为了白拿 SSE 解析、增量合并、用量字段与重试，本层只留业务口径；
2. **模型怎么连由档案给，不由 env 硬编码**：``ModelProfile`` 带着网关地址、密钥与默认参数
   （规范 L27 的配置表投影），本层只负责把它们变成厂商客户端；env 的
   ``LLM_BASE_URL`` / ``LLM_API_KEY`` 退化为「档案没写时的兜底」；
3. 只暴露 ``chat`` / ``stream_chat`` / ``stream_chat_events`` 三种出口与 ``chat_model``
   （``build_agent`` 等框架能力的取用口）；提示词模板、工具调用、结果解析都不在这一层；
4. 内网/回环目标不读代理环境变量：自建的 vLLM 在局域网里，一旦被 ``HTTP_PROXY`` 劫持
   就只剩 502（见 :func:`app.core.clients.base.trust_env_for_url`），因此显式注入 httpx 客户端；
5. 用量统计依赖 ``stream_usage=True``：网关支持时末帧带 usage，映射到 ``ChatStreamChunk`` 的
   ``tokens_in`` / ``tokens_out``；不支持的网关保持 0，不影响正文流。
"""

import hashlib
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx
from fastapi import status
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessageChunk, BaseMessage
from langchain_core.runnables import RunnableConfig

from app.config.setting import settings
from app.core.exceptions import CustomException

from .base import trust_env_for_url

if TYPE_CHECKING:  # 仅作类型：运行时不能反向依赖 core.agents（其 builder 依赖本模块）
    from app.core.agents.profiles import ModelProfile

#: 「本地部署 / OpenAI 兼容」的 provider 写法：它们与 OpenAI 的差别只在地址、不在协议，
#: 直接当 ``model_provider`` 传给 ``init_chat_model`` 会因为不认识这个名字而报错。
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


@dataclass(frozen=True, slots=True)
class ChatStreamChunk:
    """一条流式分片：增量正文 + （可能为 0 的）累计用量。

    用量只在流末尾的那一片里给全（OpenAI 兼容服务在 ``stream_options.include_usage``
    打开时的行为），因此调用方应「有值就覆盖、无值就沿用」。
    """

    delta: str = ""
    tokens_in: int = 0
    tokens_out: int = 0


def message_text(content: Any) -> str:
    """把消息内容归一为纯文本（LangChain 1.x 可能给内容块列表）。

    公开而非私有：``core/agents`` 的智能体正文流要按同一口径抽取增量，
    两处各写一份的话，内容块格式一变（如新增 reasoning 块）就会出现两种解释。
    """
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


def chat_provider(provider: str) -> str:
    """``model_provider`` → ``init_chat_model`` 认识的值（本地部署归一成 OpenAI 兼容）。

    本地自建服务（vLLM / llama.cpp / SGLang / TEI / Xinference / LM Studio）都提供
    ``/v1/chat/completions``，协议与 OpenAI 相同，但 ``init_chat_model`` 只认框架内置的
    provider 名——把 ``local`` / ``vllm`` 直接透传会抛 ``ValueError: Unsupported model_provider``。
    Ollama 协议不同，必须原样保留；其余未知值也照旧透传（让框架报出自己认识的那批名称）。
    """
    value = (provider or "").strip().lower()
    if not value:
        return "openai"
    return "openai" if value in _OPENAI_COMPATIBLE_PROVIDERS else value


class LlmClient:
    """聊天模型客户端；连接参数以档案为准，env 只在档案没写时兜底。"""

    service_name = "LLM 服务"

    def __init__(
        self,
        base_url: str | None = None,
        *,
        api_key: str | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
    ) -> None:
        self.base_url = (settings.LLM_BASE_URL if base_url is None else base_url).rstrip("/")
        self.api_key = settings.LLM_API_KEY if api_key is None else api_key
        self.timeout = settings.LLM_TIMEOUT if timeout is None else timeout
        self.max_retries = max(0, settings.LLM_MAX_RETRIES if max_retries is None else max_retries)
        # 模型实例按「供应商 + 模型名 + 网关 + 密钥指纹 + 完整参数」缓存：任一项不同就是
        # 另一份请求配置，共用一个实例会让后一次调用静默改掉前一次的设定
        self._clients: dict[tuple[Any, ...], BaseChatModel] = {}
        # 每个端点（地址 + 超时）一套 httpx 连接池，关停时统一关闭
        self._http_clients: dict[tuple[str, float], httpx.AsyncClient] = {}

    # ── 内部辅助 ──────────────────────────────────────────────────────

    def chat_model(
        self,
        profile: "ModelProfile",
        *,
        overrides: Mapping[str, Any] | None = None,
    ) -> BaseChatModel:
        """取模型实例：连接方式与默认参数来自档案，``overrides`` 是单次调用的最后一次覆盖。

        三级覆盖在本层合死（规范 L27）：档案里的 ``params`` 是配置表默认值 + 用途场景，
        ``overrides`` 是调用点自己的要求（如请求指定的 ``max_tokens``）。连接类参数
        （地址 / 密钥 / 连接池）不允许从 ``overrides`` 改，否则同一轮里会不知不觉换端点。

        ``params`` 里的 ``timeout`` 同时作为 httpx 与 SDK 的超时（网关慢的模型需要有自己
        的耐心）；没写则取 env 的 ``LLM_TIMEOUT``。
        """
        endpoint = (profile.base_url or self.base_url).rstrip("/")
        api_key = profile.api_key or self.api_key
        if not endpoint:
            raise CustomException(
                msg=(
                    f"{self.service_name} 地址未配置：模型 {profile.label} 既没有配置表 base_url，"
                    "也没有 env 的 LLM_BASE_URL"
                ),
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        if not profile.model:
            raise CustomException(
                msg="LLM 模型名称未配置（配置表 model_name 与 env 的 LLM_MODEL 都为空）",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        params: dict[str, Any] = dict(profile.params)
        params.update(overrides or {})
        timeout = float(params.pop("timeout", 0) or self.timeout)
        params["timeout"] = timeout
        params.setdefault("max_retries", self.max_retries)

        cache_key = (
            chat_provider(profile.provider),
            profile.model,
            endpoint,
            _digest(api_key),
            json.dumps(params, sort_keys=True, ensure_ascii=False, default=str),
        )
        cached = self._clients.get(cache_key)
        if cached is not None:
            return cached
        http_client = self._http_clients.get((endpoint, timeout))
        if http_client is None:
            http_client = httpx.AsyncClient(
                timeout=timeout,
                trust_env=trust_env_for_url(endpoint),
            )
            self._http_clients[(endpoint, timeout)] = http_client
        client = init_chat_model(
            profile.model,
            # 只能用通用入口选模型：厂商类（ChatOpenAI / AzureChatOpenAI）不落在业务代码里。
            # 本地部署的写法（local / vllm / tei…）先归一成 openai，否则这里会直接报 Unsupported provider。
            model_provider=chat_provider(profile.provider),
            # 私有化 vLLM 常不带密钥，而 OpenAI SDK 不允许空 key（不是密钥校验，只是必填占位）
            api_key=api_key or "not-needed",
            base_url=endpoint,
            streaming=True,
            # 末帧带 usage（映射到 usage_metadata），落库 token 统计依赖它
            stream_usage=True,
            http_async_client=http_client,
            **params,
        )
        self._clients[cache_key] = client
        return client

    async def aclose(self) -> None:
        """关闭所有端点底层的 httpx 连接池（应用关停时统一调用）。"""
        for http_client in self._http_clients.values():
            await http_client.aclose()
        self._http_clients.clear()
        self._clients.clear()

    async def chat(
        self,
        messages: Sequence[BaseMessage],
        *,
        profile: "ModelProfile",
        overrides: Mapping[str, Any] | None = None,
        config: RunnableConfig | None = None,
    ) -> str:
        """一次性拿到完整回答（离线批量、评测脚本用）。

        ``config`` 由调用方给（``run_name`` / ``metadata`` / ``tags``）：本层不自己造追踪配置，
        只保证原样透传到模型调用——不透传时模型 run 会变成 trace 里的孤立根节点，
        看不到它属于哪一轮问答。
        """
        try:
            message = await self.chat_model(profile, overrides=overrides).ainvoke(
                list(messages), config=config
            )
        except CustomException:
            raise  # 未配置/空回答：本层可读异常原样上抛，别被下面包成 502（与流式同口径）
        except Exception as exc:  # noqa: BLE001 客户端异常统一成可读的 502
            raise self._failure(exc) from exc
        content = message_text(message.content).strip()
        if not content:
            raise CustomException(
                msg=f"{self.service_name} 返回空内容",
                status_code=status.HTTP_502_BAD_GATEWAY,
            )
        return content

    async def stream_chat(
        self,
        messages: Sequence[BaseMessage],
        *,
        profile: "ModelProfile",
        overrides: Mapping[str, Any] | None = None,
        config: RunnableConfig | None = None,
    ) -> AsyncIterator[str]:
        """按 SSE 增量产出回答片段（前端打字机效果 / 长回答降首字节延迟）。"""
        async for chunk in self.stream_chat_events(
            messages, profile=profile, overrides=overrides, config=config
        ):
            if chunk.delta:
                yield chunk.delta

    async def stream_chat_events(
        self,
        messages: Sequence[BaseMessage],
        *,
        profile: "ModelProfile",
        overrides: Mapping[str, Any] | None = None,
        config: RunnableConfig | None = None,
    ) -> AsyncIterator[ChatStreamChunk]:
        """流式产出「增量正文 + 用量」：需要落库 token 统计的调用方用这个入口。"""
        try:
            stream = self.chat_model(profile, overrides=overrides).astream(
                list(messages), config=config
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


def _digest(secret: str) -> str:
    """密钥指纹：缓存键里不直接放明文，异常与调试输出不会被顺带带出去。"""
    return hashlib.sha256(secret.encode()).hexdigest()[:12] if secret else ""


def _to_stream_chunk(chunk: AIMessageChunk) -> ChatStreamChunk:
    """LangChain 增量块 → 业务分片（正文 + 用量）。"""
    usage = getattr(chunk, "usage_metadata", None) or {}
    return ChatStreamChunk(
        delta=message_text(chunk.content),
        tokens_in=int(usage.get("input_tokens") or 0),
        tokens_out=int(usage.get("output_tokens") or 0),
    )


__all__ = ["ChatStreamChunk", "LlmClient", "chat_provider", "message_text"]
