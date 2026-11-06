"""MinerU 解析产物归档：markdown / 版面 JSON / 内嵌图片统一存进对象存储。

为什么要存：解析结果是「后续使用」的原始凭据——按新提示词重提 FAQ、调整分片粒度、
前端按原图定位、审计「这条切片出自原文哪一段」，都要拿得回当时的完整产物。
只把 ``text`` 落进 ``parse_result`` 会丢掉版面结构与图片（``images/`` 在 Markdown 里
是相对路径，脱离这份快照就再也对不上）。

键布局（一次解析一份完整快照，全局前缀由 ``S3_KEY_PREFIX`` 再加）：

    parse-artifacts/<job_id>/full.md            解析正文（MinerU 为版面还原后的 Markdown）
    parse-artifacts/<job_id>/content_list.json  版面块列表（精准解析才有，flash 模式为空）
    parse-artifacts/<job_id>/images/001-<name>  内嵌图片（序号前缀避免同名覆盖）

口径：对象存储未启用时归档直接跳过（步骤台账记 skipped）；上传失败抛给调用方，
由流水线降级成告警并记 failed（fatal=false）——产物归档是增强能力，不能拖垮解析本身。
"""

import json
import mimetypes
import re
from pathlib import PurePosixPath
from typing import Any

from app.config.setting import settings
from app.core.clients.s3 import S3Client, get_s3_client
from app.core.logger import logger
from app.core.rag.parsers import ParsedDocument

# 对象键前缀：与原件（parse-sources）、页图（parse-previews）隔离，便于各自配生命周期
_KEY_PREFIX = "parse-artifacts"

MARKDOWN_NAME = "full.md"
CONTENT_LIST_NAME = "content_list.json"

# ``job_id`` / 图片名都来自外部，做白名单替换，杜绝 ``../`` 之类的键穿越
_UNSAFE_JOB_ID = re.compile(r"[^A-Za-z0-9._-]")
_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._-]")


def artifacts_available() -> bool:
    """是否具备归档能力：仅取决于对象存储是否接入（不受页图开关影响）。"""
    return bool(settings.S3_ENABLE)


def _prefix(job_id: str) -> str:
    """该任务的对象键前缀（不含 ``S3_KEY_PREFIX``，与 ``presign_get`` 入参同口径）。"""
    return f"{_KEY_PREFIX}/{_UNSAFE_JOB_ID.sub('_', job_id)}"


def _image_name(name: str, index: int) -> str:
    """图片键文件名：序号 + 安全名，保留后缀（同名图不会互相覆盖）。"""
    pure = PurePosixPath(str(name or "").replace("\\", "/")).name
    stem = _UNSAFE_NAME.sub("_", pure).strip("_") or "image.png"
    if "." not in stem:
        stem = f"{stem}.png"
    return f"{index:03d}-{stem}"


def archive_document(
    job_id: str,
    document: ParsedDocument,
    *,
    client: S3Client | None = None,
) -> dict[str, Any]:
    """把一次解析的完整产物上传到对象存储，返回可落库的键与计数。

    同步阻塞（boto3 上传走网络），调用方请用 ``asyncio.to_thread`` 包一层。
    不做部分成功：任一件失败都抛异常，避免落库一份「一半能取、一半 404」的快照。
    """
    bucket = client or get_s3_client()
    prefix = _prefix(job_id)

    markdown_key = f"{prefix}/{MARKDOWN_NAME}"
    bucket.upload_bytes(
        document.text.encode("utf-8"),
        markdown_key,
        content_type="text/markdown; charset=utf-8",
    )

    content_list_key: str | None = None
    if document.content_list:
        content_list_key = f"{prefix}/{CONTENT_LIST_NAME}"
        bucket.upload_bytes(
            _json_bytes(document.content_list),
            content_list_key,
            content_type="application/json; charset=utf-8",
        )

    image_keys: list[str] = []
    for index, image in enumerate(document.images, start=1):
        key = f"{prefix}/images/{_image_name(image.name, index)}"
        content_type = mimetypes.guess_type(key)[0] or "application/octet-stream"
        bucket.upload_bytes(image.data, key, content_type=content_type)
        image_keys.append(key)

    logger.info(
        "MinerU 产物已归档：job_id={} markdown=1 json={} images={}",
        job_id,
        1 if content_list_key else 0,
        len(image_keys),
    )
    return {
        "prefix": prefix,
        "markdown_key": markdown_key,
        "content_list_key": content_list_key,
        "image_keys": image_keys,
        "image_count": len(image_keys),
        "source_name": document.source,
        "parser": str(document.meta.get("parser") or ""),
    }


def presign_artifacts(
    artifacts: dict[str, Any] | None,
    *,
    ttl_seconds: int | None = None,
) -> dict[str, Any] | None:
    """把落库的键投影成预签名 URL（给管理端详情用）；无产物或未接对象存储返回 None。

    ``presign_get`` 只做本地签名计算、不发网络请求，可直接在同步投影里调用。
    """
    if not artifacts or not artifacts_available():
        return None
    try:
        bucket = get_s3_client()
        expire = settings.PARSE_PREVIEW_TTL_SECONDS if ttl_seconds is None else ttl_seconds
        markdown_key = str(artifacts.get("markdown_key") or "")
        content_list_key = str(artifacts.get("content_list_key") or "")
        rows = {
            "markdown_key": markdown_key,
            "markdown_url": bucket.presign_get(markdown_key, expires_in=expire) if markdown_key else None,
            "content_list_key": content_list_key or None,
            "content_list_url": bucket.presign_get(content_list_key, expires_in=expire) if content_list_key else None,
            "images": [
                {"object_key": key, "image_url": bucket.presign_get(key, expires_in=expire)}
                for key in artifacts.get("image_keys") or []
            ],
            "image_count": int(artifacts.get("image_count") or 0),
            "parser": artifacts.get("parser") or None,
        }
    except Exception as exc:  # noqa: BLE001 签名失败不该让详情页整页报错
        logger.warning("MinerU 产物预签名失败：{}", exc)
        return None
    return rows


def _json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")