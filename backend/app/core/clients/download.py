"""文件下载客户端：按 URL 流式拉取并限制字节上限（`/v1` 解析受理期使用）。

放在 core 层的原因与其它出站客户端一致：``/v1`` 接入层与后续业务域都可能按 URL 取文件，
放在任一业务域会迫使其它域反向依赖。实例同样遵循「进程级单例 + lifespan 关停」：

1. 代理口径按目标地址区分（见 ``base.trust_env_for_url``）：内网/回环直连，公网地址允许读
   ``HTTP_PROXY``；``httpx.AsyncClient`` 的 ``trust_env`` 构造后不可变，因此按该布尔值缓存两个实例，
   而不是每次请求新建一个连接池；
2. 核心层只抛 ``DownloadError``，由接入层翻译成契约错误码（core 不认识 HTTP 语义）。
"""

import urllib.parse

import httpx

from app.core.clients.base import trust_env_for_url

DOWNLOAD_TIMEOUT_SECONDS: float = 30.0

# 键为 trust_env（是否读取系统代理环境变量），值为对应口径的进程级实例
_clients: dict[bool, httpx.AsyncClient] = {}


class DownloadError(Exception):
    """下载失败：``too_large`` 为真表示超过字节上限，其余均视为地址不可用。"""

    def __init__(self, message: str, *, too_large: bool = False) -> None:
        super().__init__(message)
        self.too_large = too_large


def get_download_client(url: str) -> httpx.AsyncClient:
    """取该地址对应代理口径的进程级下载客户端（首次调用时创建）。"""
    trust_env = trust_env_for_url(url)
    client = _clients.get(trust_env)
    if client is None:
        client = httpx.AsyncClient(
            timeout=DOWNLOAD_TIMEOUT_SECONDS,
            follow_redirects=True,
            trust_env=trust_env,
        )
        _clients[trust_env] = client
    return client


async def download_bytes(url: str, *, max_bytes: int, timeout: float | None = None) -> bytes:
    """流式下载 ``url`` 并在超过 ``max_bytes`` 时立刻中断。

    不先读完整文件再判断：超大文件在校验期就该失败（否则 10MB 上限形同虚设），
    且调用方只会在 202 之后看到一个永远 queued 的任务。

    异常:
    - DownloadError: 地址非法 / 状态码非 200 / 网络失败（``too_large=False``）；
      或声明/实际字节超过上限（``too_large=True``）。
    """
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise DownloadError("file_url 必须为 http/https 地址。")

    client = get_download_client(url)
    buffer = bytearray()
    try:
        async with client.stream("GET", url, timeout=timeout) as response:
            if response.status_code != httpx.codes.OK:
                raise DownloadError(f"file_url 读取失败（HTTP {response.status_code}），请确认地址有效。")
            declared = response.headers.get("content-length", "")
            if declared.isdigit() and int(declared) > max_bytes:
                raise DownloadError(f"文件超过 {max_bytes // (1024 * 1024)}MB 上限。", too_large=True)
            async for part in response.aiter_bytes():
                buffer.extend(part)
                if len(buffer) > max_bytes:
                    raise DownloadError(f"文件超过 {max_bytes // (1024 * 1024)}MB 上限。", too_large=True)
    except httpx.HTTPError as exc:
        raise DownloadError("file_url 无法访问，请确认解析服务可访问该地址。") from exc
    return bytes(buffer)


async def close_download_clients() -> int:
    """关闭下载客户端，返回关闭数量（应用关停时由 ``close_clients`` 统一调用）。"""
    from app.core.logger import logger

    closed = 0
    for trust_env, client in list(_clients.items()):
        try:
            await client.aclose()
            closed += 1
        except Exception as e:  # noqa: BLE001 关停阶段一律只告警
            logger.warning("关闭下载客户端失败 trust_env={}: {}", trust_env, e)
    _clients.clear()
    return closed


__all__ = ["DOWNLOAD_TIMEOUT_SECONDS", "DownloadError", "download_bytes", "get_download_client"]
