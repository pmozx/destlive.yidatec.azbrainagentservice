"""出站 HTTP 客户端基座：统一超时、重试、错误归一与耗时日志。

约定：
1. 地址留空即视为"未接入"，调用时抛 503 可读异常，不做静默降级；
2. 仅对超时/网络错误/429/5xx 重试，4xx 属调用方问题，立即失败（避免把参数错误重试成风暴）；
3. 重试间隔按指数退避且封顶 4s，重试次数由各类服务配置决定；
4. 内网/回环目标不读代理环境变量（见 :func:`trust_env_for_url`）：自建的向量化、重排序、
   vLLM 都在局域网里，一旦被 ``HTTP_PROXY`` 劫持到本机代理，代理到不了内网就只剩 502。
"""

import asyncio
import ipaddress
import time
import urllib.parse
from collections.abc import Mapping
from typing import Any

import httpx
from fastapi import status

from app.core.exceptions import CustomException
from app.core.logger import logger

# 可重试的 HTTP 状态：限流与服务端瞬时故障
_RETRYABLE_STATUS: frozenset[int] = frozenset({429, 500, 502, 503, 504})
_MAX_BACKOFF_SECONDS: float = 4.0

# 本机/回环主机名（不含 IP，IP 走 ipaddress 判定）
_LOCAL_HOSTS: frozenset[str] = frozenset({"localhost", "localhost.localdomain", "::1", "0.0.0.0"})


def trust_env_for_url(url: str) -> bool:
    """是否允许该请求读取 ``HTTP_PROXY`` / ``ALL_PROXY`` 等系统代理环境变量。

    局域网与容器网络里的自建模型服务必须直连：实测同一条请求经本机代理转发会在 30s
    后返回空体 502（代理到不了内网），直连则正常。公网 API（如 SaaS 大模型）仍需走代理，
    所以按目标地址区分，不做一刀切。
    """
    try:
        host = (urllib.parse.urlsplit(url).hostname or "").strip().lower()
    except ValueError:
        return True
    if not host:
        return True
    if host in _LOCAL_HOSTS:
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # 容器服务名 / 单段主机名（service-brain-ollama）不可能是公网域名，按内网直连处理
        return "." in host
    return not (address.is_private or address.is_loopback or address.is_link_local)


class BaseHttpClient:
    """HTTP 客户端基座（每个子类对应一类外部服务）。"""

    service_name: str = "集成服务"

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 60.0,
        max_retries: int = 2,
        api_key: str = "",
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.timeout = timeout
        self.max_retries = max(0, max_retries)
        self.api_key = api_key

    # ── 内部辅助 ──────────────────────────────────────────────────────

    def _require_base_url(self) -> None:
        """地址未配置时快速失败：报错信息要能直接告诉运维该填哪个环境变量。"""
        if not self.base_url:
            raise CustomException(
                msg=f"{self.service_name} 地址未配置，请先在 backend/env/.env.<env> 填写对应 BASE_URL",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def post_json(
        self,
        path: str,
        payload: Mapping[str, Any],
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """POST JSON 并返回解析后的字典，失败按约定重试。"""
        self._require_base_url()
        url = self._url(path)
        last_error = ""
        timeout_value = timeout or self.timeout

        for attempt in range(self.max_retries + 1):
            start = time.perf_counter()
            try:
                async with httpx.AsyncClient(timeout=timeout_value, trust_env=trust_env_for_url(url)) as client:
                    response = await client.post(url, json=dict(payload), headers=self._headers())
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            else:
                elapsed_ms = (time.perf_counter() - start) * 1000
                if response.status_code < 400:
                    try:
                        return response.json()
                    except ValueError:
                        raise CustomException(
                            msg=f"{self.service_name} 返回非 JSON 响应（{url}）：{response.text[:200]}",
                            status_code=status.HTTP_502_BAD_GATEWAY,
                        ) from None
                last_error = f"HTTP {response.status_code}: {response.text[:200]}"
                if response.status_code not in _RETRYABLE_STATUS:
                    raise CustomException(
                        msg=f"{self.service_name} 调用失败（{last_error}）",
                        status_code=status.HTTP_502_BAD_GATEWAY,
                    )
                logger.warning(
                    "{} 第 {}/{} 次调用失败（{}，耗时 {:.0f}ms）",
                    self.service_name,
                    attempt + 1,
                    self.max_retries + 1,
                    last_error,
                    elapsed_ms,
                )

            if attempt < self.max_retries:
                await asyncio.sleep(min(2**attempt * 0.5, _MAX_BACKOFF_SECONDS))

        raise CustomException(
            msg=f"{self.service_name} 重试 {self.max_retries} 次后仍失败（{last_error}）",
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
        )
