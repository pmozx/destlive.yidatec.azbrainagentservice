"""解析页图预览：落盘、签名 URL 与访问校验（T2.4）。

口径：
- 页图按 ``job_id`` 分目录存放在 ``static/parse-previews/<job_id>/page-<n>.png``；
- `#02` 返回的 ``image_url`` 带 ``expires`` 与 ``sig``，签名覆盖 ``job_id:file_name:expires``，
  默认 7 天有效；调用方不需要携带契约签名头即可预览；
- ``image_url`` 的基址取 ``PARSE_PREVIEW_BASE_URL``；未配置时自动探测本机网卡 IP + 端口，
  保证返回的是服务自身可被外部访问的地址（不受调用方 Host 头影响）；
- 过期、签名不符、路径不合法一律按 404 处理，不泄漏目录结构；
- 无签名密钥时不生成 URL（能力缺失按契约返回 ``pages=[]``，不伪造图像地址）。
"""

import hashlib
import hmac
import re
import socket
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

from app.config.path_conf import STATIC_DIR
from app.config.setting import settings
from app.core.logger import logger
from app.core.rag.page_render import RenderedPage

PREVIEW_ROUTE_PREFIX = "/previews/parse"
_PREVIEW_DIR = STATIC_DIR / "parse-previews"
_PAGE_FILE = re.compile(r"^page-\d+\.png$")
_UNSAFE_JOB_ID = re.compile(r"[^A-Za-z0-9._-]")
_detected_base_url: str | None = None


def _secret() -> str:
    return (settings.PARSE_PREVIEW_SECRET or settings.CONTRACT_V1_SHARED_SECRET).strip()


def _detect_lan_ip() -> str:
    """探测本机对外网卡的 IPv4：UDP 只取路由结果，不实际发包；失败回落到主机名解析。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        pass
    try:
        return socket.gethostbyname(socket.gethostname())
    except OSError:
        return "127.0.0.1"


def public_base_url() -> str:
    """页图 URL 的对外基址。

    优先取 ``PARSE_PREVIEW_BASE_URL``（容器部署、端口映射与域名场景必须显式配置）；
    留空时用本机真实网卡 IP + ``PORT``，避免回落到调用方 Host 头（可能是 127.0.0.1 或内网别名）。
    """
    global _detected_base_url
    configured = settings.PARSE_PREVIEW_BASE_URL.strip()
    if configured:
        return configured.rstrip("/")
    if _detected_base_url is None:
        _detected_base_url = f"http://{_detect_lan_ip()}:{settings.PORT}"
        logger.info("解析页图预览基址自动探测为 {}（如需外部访问请配置 PARSE_PREVIEW_BASE_URL）", _detected_base_url)
    return _detected_base_url


def apply_public_base(image_url: str) -> str:
    """把已落库的页图 URL 换成当前对外基址。

    只处理本服务的预览路径，``expires`` / ``sig`` 原样保留；用于旧任务（曾按
    127.0.0.1 或调用方 Host 生成）以及换 IP / 端口 / 域名后的自愈，无需重新解析。
    """
    parts = urlsplit(image_url)
    if not parts.path.startswith(PREVIEW_ROUTE_PREFIX):
        return image_url
    base = urlsplit(public_base_url())
    prefix = base.path.rstrip("/")
    return urlunsplit((base.scheme, base.netloc, f"{prefix}{parts.path}", parts.query, ""))


def pages_with_public_base(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """批量把页图数组的 ``image_url`` 投影到当前对外基址（无 image_url 的项原样保留）。"""
    return [
        {**page, "image_url": apply_public_base(page["image_url"])} if page.get("image_url") else page
        for page in pages
    ]


def preview_available() -> bool:
    """是否具备页图能力：开关打开且有签名密钥。"""
    return bool(settings.PARSE_PREVIEW_ENABLE and _secret())


def _signature(job_id: str, file_name: str, expires: int) -> str:
    message = f"{job_id}:{file_name}:{expires}".encode()
    return hmac.new(_secret().encode(), message, hashlib.sha256).hexdigest()


def _job_dir(job_id: str) -> Path:
    """任务目录名做白名单替换，杜绝 ``../`` 穿越。"""
    return _PREVIEW_DIR / _UNSAFE_JOB_ID.sub("_", job_id)


def save_pages(
    job_id: str,
    pages: list[RenderedPage],
    *,
    base_url: str | None = None,
    ttl_seconds: int | None = None,
) -> list[dict[str, Any]]:
    """把渲染结果落盘并返回契约 ``pages`` 结构；未启用页图能力时返回空列表。"""
    if not pages or not preview_available():
        return []

    expires = int(time.time()) + (settings.PARSE_PREVIEW_TTL_SECONDS if ttl_seconds is None else ttl_seconds)
    directory = _job_dir(job_id)
    directory.mkdir(parents=True, exist_ok=True)
    base = (base_url or public_base_url()).rstrip("/")
    rows: list[dict[str, Any]] = []
    for page in pages:
        file_name = f"page-{page.page_no}.png"
        (directory / file_name).write_bytes(page.data)
        signature = _signature(job_id, file_name, expires)
        query = urlencode({"expires": expires, "sig": signature})
        rows.append(
            {
                "page_no": page.page_no,
                "width": page.width,
                "height": page.height,
                "image_url": f"{base}{PREVIEW_ROUTE_PREFIX}/{quote(job_id, safe='')}/{file_name}?{query}",
            }
        )
    return rows


def preview_file_path(job_id: str, file_name: str) -> Path | None:
    """合法页图文件路径；任务目录与文件名不合法时返回 None。"""
    if not _PAGE_FILE.match(file_name):
        return None
    return _job_dir(job_id) / file_name


def verify_preview_request(*, job_id: str, file_name: str, expires: int, signature: str) -> bool:
    """校验有效期与签名（恒时比较），任何一项不满足都返回 False。"""
    if not preview_available() or expires < int(time.time()):
        return False
    return hmac.compare_digest(signature, _signature(job_id, file_name, expires))
