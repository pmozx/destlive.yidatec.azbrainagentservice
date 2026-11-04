"""解析页图预览：渲染结果上传对象存储，读取时按需重新预签名（T2.4）。

口径：
- 页图不再落本地磁盘：上传到系统已集成的 S3 兼容对象存储（``app/core/clients/s3.py``），
  对象键为 ``parse-previews/<job_id>/page-<n>.png``（全局前缀由 ``S3_KEY_PREFIX`` 再加），
  对象键随产物一起落库（``pages[*].object_key``）；
- `#02` 返回的 ``image_url`` 是对象存储的**预签名 GET URL**，有效期取
  ``PARSE_PREVIEW_TTL_SECONDS``；调用方不需要携带契约签名头即可预览；
- 预签名 URL 会过期，所以每次投影响应都按 ``object_key`` 重新签发（``refresh_pages``）；
  旧产物没有 ``object_key`` 时原样回放库里那份 URL，属历史数据，不再续签；
- 关掉 ``PARSE_PREVIEW_ENABLE`` 或未接入对象存储（``S3_ENABLE=false``）时返回 ``[]``——
  能力缺失按契约返回空数组，不伪造图像地址。
"""

import re
from typing import Any

from app.config.setting import settings
from app.core.clients.s3 import S3Client, get_s3_client
from app.core.logger import logger
from app.core.rag.page_render import RenderedPage

# 对象键前缀：与桶内的文档原件等隔离，便于按生命周期策略单独清理
_KEY_PREFIX = "parse-previews"

# 已下线的本地预览路由（旧产物里存的是这种地址），投影时识别出来并丢弃
_LEGACY_PREVIEW_PATH = "/previews/parse/"

# ``job_id`` 来自调用方，做白名单替换，杜绝 ``../`` 之类的键穿越
_UNSAFE_JOB_ID = re.compile(r"[^A-Za-z0-9._-]")


def preview_available() -> bool:
    """是否具备页图能力：开关打开且对象存储已接入。"""
    return bool(settings.PARSE_PREVIEW_ENABLE and settings.S3_ENABLE)


def _object_key(job_id: str, page_no: int) -> str:
    """页图对象键（不含 ``S3_KEY_PREFIX``，与 ``presign_get`` 的入参口径一致）。"""
    return f"{_KEY_PREFIX}/{_UNSAFE_JOB_ID.sub('_', job_id)}/page-{page_no}.png"


def upload_pages(
    job_id: str,
    pages: list[RenderedPage],
    *,
    ttl_seconds: int | None = None,
    client: S3Client | None = None,
) -> list[dict[str, Any]]:
    """上传渲染结果并返回契约 ``pages`` 结构；未启用页图能力时返回空列表。

    同步阻塞（boto3 上传走网络），调用方请用 ``asyncio.to_thread`` 包一层。
    整批上传不做部分成功：任一张失败都抛异常，由上层降级成「未返回预览图」的告警，
    避免调用方拿到一半可用一半 404 的页图数组。
    """
    if not pages or not preview_available():
        return []

    bucket = client or get_s3_client()
    expire = settings.PARSE_PREVIEW_TTL_SECONDS if ttl_seconds is None else ttl_seconds
    rows: list[dict[str, Any]] = []
    for page in pages:
        key = _object_key(job_id, page.page_no)
        bucket.upload_bytes(page.data, key, content_type="image/png")
        rows.append(
            {
                "page_no": page.page_no,
                "width": page.width,
                "height": page.height,
                "image_url": bucket.presign_get(key, expires_in=expire),
                # 内部字段：只用于读取时重新签发，投影响应前由 refresh_pages 去掉
                "object_key": key,
            }
        )
    logger.info("解析页图已上传对象存储：job_id={} 共 {} 页", job_id, len(rows))
    return rows


def refresh_pages(
    pages: list[dict[str, Any]],
    *,
    ttl_seconds: int | None = None,
) -> list[dict[str, Any]]:
    """把落库的页图数组投影成对外结构：按 ``object_key`` 重签 URL，并去掉内部字段。

    ``presign_get`` 只做本地签名计算，不发网络请求，因此可直接在同步投影函数里调用。
    对象存储不可用时保留库里那份 URL（可能已过期），只告警，不影响整个 ``#02`` 响应。
    """
    if not pages:
        return []

    expire = settings.PARSE_PREVIEW_TTL_SECONDS if ttl_seconds is None else ttl_seconds
    available = preview_available()
    client: S3Client | None = None
    rows: list[dict[str, Any]] = []
    for page in pages:
        item = {key: value for key, value in page.items() if key != "object_key"}
        object_key = str(page.get("object_key") or "").strip()
        if object_key:
            if available:
                try:
                    client = client or get_s3_client()
                    item["image_url"] = client.presign_get(object_key, expires_in=expire)
                except Exception as exc:  # noqa: BLE001 预览失败不影响解析结果
                    logger.warning("页图预签名失败（object_key={}）：{}", object_key, exc)
        elif _LEGACY_PREVIEW_PATH in str(page.get("image_url") or ""):
            # 旧版本的本地静态预览路由已下线，宁可不返回，也不回放必然 404 的链接
            logger.info("跳过旧版本本地页图（job_id 不可追溯，object_key 缺失）：{}", page.get("page_no"))
            continue
        rows.append(item)
    return rows
