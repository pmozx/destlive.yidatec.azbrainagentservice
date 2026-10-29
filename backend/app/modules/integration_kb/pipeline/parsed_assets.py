"""解析产物（Markdown / 版面 JSON / 内嵌图片）的对象键布局与图片引用处理。

为什么要单独留存产物：分片只保留「能被检索的碎片」，无法还原版面；MinerU 结果包里的
``full.md`` 以内嵌图片引用（``images/xxx.jpg``）表达图文排版，图片不跟着存就等于丢了
一半内容。产物布局：

- 存储布局：``{前缀}/spaces/{空间}/{文档}/parsed/full.md`` +
  ``parsed/content_list.json`` + ``parsed/images/{文件名}``；Markdown 内保留
  ``images/{文件名}`` 相对引用，整目录自包含，下载解压即可直接渲染，
  也不把易变的访问地址（本机域名/鉴权参数）固化进产物；
- 预览展示：服务层读取 full.md 后，按桶内**实际存在**的图片对象把相对引用重写成
  读取接口地址（见 :func:`preview_image_resolver`），避免前端发起无效请求；
- 安全约束：图片文件名一律归一化为单层 basename 且限定图片后缀，杜绝
  ``../`` 路径穿越与非图片对象读取。

本模块是纯函数工具（不发网络请求），供流水线与空间文档服务共用。
"""

import mimetypes
import re
from collections.abc import Callable, Iterable
from pathlib import PurePosixPath
from urllib.parse import quote

from app.config.setting import settings

# 产物目录（相对文档目录）：与上传原件（spaces/{空间}/{文档}/{原文件名}）并列
PARSED_DIR = "parsed"
PARSED_MARKDOWN_NAME = "full.md"
PARSED_CONTENT_LIST_NAME = "content_list.json"
PARSED_IMAGES_DIR = "images"
# 续跑快照：解析成功后落一份（正文/版面/产物键），分片成功后再补写分片清单，
# 后续节点失败时原地续跑，不必再次调用 MinerU
PARSED_RESULT_NAME = "parse_result.json"

# 允许的图片后缀（与 MinerU 结果包内图片类型一致）
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".bmp", ".svg", ".webp"})

# 图片读取接口路由（相对 API 前缀）：与 document/controller.py 的 image 端点一致
ASSET_ROUTE_PREFIX = "/integration-kb/document/image"

# Markdown 图片语法：![alt](src) / ![alt](<src>) / ![alt](src "title")
_MD_IMAGE_RE = re.compile(r"(!\[[^\]]*\]\(\s*)(<[^>]*>|[^)\s]+)([^)]*\))")
# 原始 HTML 图片标签：<img src="..."> / <img ... src='...'>
_HTML_IMAGE_RE = re.compile(r"(<img\b[^>]*?\bsrc\s*=\s*)([\"'])(.*?)\2", re.IGNORECASE)


def _api_prefix() -> str:
    """接口前缀（延迟导入避免与 ``app.api.v1.routers`` 形成循环：那边要导入本域控制器）。"""
    from app.api.v1.routers import API_PREFIX

    return API_PREFIX


def parsed_prefix(space_code: str, doc_id: int | str) -> str:
    """产物目录前缀（``{前缀}/spaces/{空间}/{文档}/parsed``）。"""
    parts = [settings.S3_KEY_PREFIX.strip("/"), "spaces", space_code, str(doc_id), PARSED_DIR]
    return "/".join(part for part in parts if part)


def parsed_markdown_key(space_code: str, doc_id: int | str) -> str:
    """产物 Markdown 对象键。"""
    return f"{parsed_prefix(space_code, doc_id)}/{PARSED_MARKDOWN_NAME}"


def parsed_content_list_key(space_code: str, doc_id: int | str) -> str:
    """产物版面 JSON 对象键。"""
    return f"{parsed_prefix(space_code, doc_id)}/{PARSED_CONTENT_LIST_NAME}"


def parsed_result_key(space_code: str, doc_id: int | str) -> str:
    """续跑快照对象键（解析正文 + 版面 + 产物键 + 分片清单）。"""
    return f"{parsed_prefix(space_code, doc_id)}/{PARSED_RESULT_NAME}"


def parsed_images_prefix(space_code: str, doc_id: int | str) -> str:
    """产物图片目录前缀（列举桶内图片依据，注意末尾带 /）。"""
    return f"{parsed_prefix(space_code, doc_id)}/{PARSED_IMAGES_DIR}/"


def parsed_image_key(space_code: str, doc_id: int | str, name: str) -> str:
    """产物图片对象键（name 须为 :func:`safe_image_name` 归一化后的文件名）。"""
    return f"{parsed_images_prefix(space_code, doc_id)}{name}"


def safe_image_name(name: str) -> str | None:
    """归一化图片文件名：仅保留单层 basename，且后缀必须属于图片白名单。

    空值、隐藏文件、非图片后缀或目录形态（以 / 结尾）返回 None。
    """
    raw = (name or "").strip().split("?", 1)[0].split("#", 1)[0].replace("\\", "/")
    if not raw or raw.endswith("/"):
        return None
    base = PurePosixPath(raw).name
    if not base or base.startswith("."):
        return None
    if PurePosixPath(base).suffix.lower() not in IMAGE_SUFFIXES:
        return None
    return base


def image_basename(ref: str) -> str | None:
    """从 Markdown/HTML 图片引用中提取可归一化的文件名（外链与内联数据不处理）。"""
    src = (ref or "").strip()
    if not src or src.startswith(("data:", "http://", "https://", "//", "/")):
        return None
    return safe_image_name(src)


def rewrite_image_refs(text: str, resolver: Callable[[str], str | None]) -> str:
    """按 resolver 重写文本中的 Markdown 与 HTML 图片引用（返回 None 表示保持原样）。"""

    def _replace_md(match: re.Match[str]) -> str:
        raw = match.group(2)
        if raw.startswith("<") and raw.endswith(">"):
            target = resolver(raw[1:-1])
            return f"{match.group(1)}<{target}>{match.group(3)}" if target else match.group(0)
        target = resolver(raw)
        return f"{match.group(1)}{target}{match.group(3)}" if target else match.group(0)

    def _replace_html(match: re.Match[str]) -> str:
        target = resolver(match.group(3))
        quote_char = match.group(2)
        return f"{match.group(1)}{quote_char}{target}{quote_char}" if target else match.group(0)

    return _HTML_IMAGE_RE.sub(_replace_html, _MD_IMAGE_RE.sub(_replace_md, text))


def normalize_markdown_image_refs(markdown: str, stored_names: Iterable[str]) -> str:
    """把 Markdown 图片引用统一为 ``images/{已入库文件名}`` 相对路径。

    结果包内的引用可能带目录前缀（``xxx/images/img_0.jpg``）或已为
    ``images/img_0.jpg``；统一按 basename 对齐到实际入库文件名，保证 full.md
    与桶内 images/ 目录一一对应。未入库（含外链）的引用保持原样。
    """
    known = set(stored_names)

    def _resolve(ref: str) -> str | None:
        base = image_basename(ref)
        if base and base in known:
            return f"{PARSED_IMAGES_DIR}/{base}"
        return None

    return rewrite_image_refs(markdown, _resolve)


def normalize_content_list_image_refs(content_list: list[dict] | None, stored_names: Iterable[str]) -> list[dict]:
    """把版面 JSON 中图片块的 ``img_path`` 统一为 ``images/{已入库文件名}``。"""
    if not content_list:
        return []
    known = set(stored_names)
    normalized: list[dict] = []
    for item in content_list:
        if isinstance(item, dict):
            img_path = item.get("img_path")
            base = image_basename(img_path) if isinstance(img_path, str) else None
            if base and base in known:
                item = {**item, "img_path": f"{PARSED_IMAGES_DIR}/{base}"}
        normalized.append(item)
    return normalized


def asset_url(doc_id: int | str, name: str) -> str:
    """图片读取接口地址（含 ``/api/v1`` 前缀，前端 <img> 可直接使用）。"""
    return f"{_api_prefix()}{ASSET_ROUTE_PREFIX}/{doc_id}/{quote(name, safe='')}"


def preview_image_resolver(doc_id: int | str, available_names: Iterable[str]) -> Callable[[str], str | None]:
    """构造预览用图片引用解析器：仅重写桶内确实存在的图片。"""
    known = set(available_names)

    def _resolve(ref: str) -> str | None:
        base = image_basename(ref)
        if base and base in known:
            return asset_url(doc_id, base)
        return None

    return _resolve


def image_content_type(name: str) -> str:
    """按图片文件名推断 MIME 类型（未知后缀回退 application/octet-stream）。"""
    return mimetypes.guess_type(name)[0] or "application/octet-stream"
