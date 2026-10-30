"""解析器注册表：按文件后缀分发，未接入的类型明确报错，而不是返回空文本。

内置能力（零新增依赖）：
- 纯文本 / Markdown / CSV / JSON / YAML：按 UTF-8(BOM) → UTF-8 → GB18030 顺序解码；
- HTML：剥离标签与 script/style，块级标签转换为换行。

版面类二进制文档（PDF / Word / PPT / 图片）由可插拔解析器接管（如 MinerU，见
:mod:`app.core.rag.mineru`），由 ``install_mineru_parser()`` 在应用启动时注册；
未安装时这里会明确报 415，而不是返回空文本或静默降级成本地轻量提取。

本模块只依赖异常基类，不感知业务表与具体存储，可被知识平台、问答服务直接复用。
"""

from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from fastapi import status

from app.core.exceptions import CustomException

_TEXT_ENCODINGS: tuple[str, ...] = ("utf-8-sig", "utf-8", "gb18030")


@dataclass(frozen=True)
class ParsedImage:
    """解析产物中的内嵌图片（只保留文件名与字节，不保留结果包内目录）。"""

    name: str
    data: bytes


@dataclass(frozen=True)
class ParsedDocument:
    """解析结果：下游分片只吃 ``text``，``meta`` 会原样带到分片上。

    ``page_map`` 是「段落文本 → 页码」映射（版面 JSON 提供），
    供流水线给每个分片回填 ``page_no``；无页码信息的解析器留空即可。

    ``content_list`` / ``images`` 是可选产物（仅精准解析提供）：
    调用方可把它们连同 Markdown 一起落到对象存储，供在线预览与原样下载；
    纯文本解析器两者均为空。
    """

    text: str
    source: str
    suffix: str
    meta: dict[str, Any] = field(default_factory=dict)
    page_map: list[tuple[str, int]] = field(default_factory=list)
    content_list: list[dict[str, Any]] = field(default_factory=list)
    images: list[ParsedImage] = field(default_factory=list)


@runtime_checkable
class DocumentParser(Protocol):
    """解析器协议：按后缀声明能力，parse 内不得做**数据库**操作。

    ``data`` 为可选的预读字节（来自上传流/对象存储）：为 None 时由解析器自行读文件，
    避免本地文件被白白读两遍；有值时解析器必须以 ``data`` 为准（文件名仍取 ``path.name``）。
    """

    name: str
    suffixes: tuple[str, ...]

    def parse(self, path: Path, data: bytes | None) -> ParsedDocument: ...


def read_document_bytes(path: Path) -> bytes:
    """读取文档字节；失败转成 400 可读异常（路径、权限、文件缺失都在这里收口）。"""
    try:
        return path.read_bytes()
    except OSError as exc:
        raise CustomException(msg=f"文档读取失败：{path}（{exc}）", status_code=status.HTTP_400_BAD_REQUEST) from exc


class PlainTextParser:
    """纯文本类解析器（Markdown/CSV/JSON 均按文本处理，结构信息留给分片阶段）。"""

    name = "plain-text"
    suffixes: tuple[str, ...] = (".txt", ".md", ".markdown", ".csv", ".log", ".json", ".yaml", ".yml")

    def parse(self, path: Path, data: bytes | None) -> ParsedDocument:
        payload = data if data is not None else read_document_bytes(path)
        for encoding in _TEXT_ENCODINGS:
            try:
                text = payload.decode(encoding)
            except UnicodeDecodeError:
                continue
            return ParsedDocument(
                text=text,
                source=path.name,
                suffix=path.suffix.lower(),
                meta={"parser": self.name, "encoding": encoding},
            )
        raise CustomException(
            msg=f"无法识别文本编码（已尝试 {'/'.join(_TEXT_ENCODINGS)}）：{path.name}",
            status_code=status.HTTP_400_BAD_REQUEST,
        )


class _HtmlTextExtractor(HTMLParser):
    """轻量 HTML 转文本：跳过脚本样式，块级标签补换行。"""

    _SKIP_TAGS: frozenset[str] = frozenset({"script", "style", "noscript", "template"})
    _BLOCK_TAGS: frozenset[str] = frozenset(
        {"p", "div", "br", "li", "tr", "td", "th", "section", "article", "table", "h1", "h2", "h3", "h4", "h5", "h6"}
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth: int = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
        elif tag in self._BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in self._BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = data.strip()
        if text:
            self._parts.append(text)

    def get_text(self) -> str:
        return "".join(self._parts)


class HtmlTextParser:
    """HTML 解析器（标签剥离，保留可读正文）。"""

    name = "html-text"
    suffixes: tuple[str, ...] = (".html", ".htm")

    def parse(self, path: Path, data: bytes | None) -> ParsedDocument:
        payload = data if data is not None else read_document_bytes(path)
        html = payload.decode("utf-8", errors="ignore")
        extractor = _HtmlTextExtractor()
        extractor.feed(html)
        extractor.close()
        return ParsedDocument(
            text=extractor.get_text(),
            source=path.name,
            suffix=path.suffix.lower(),
            meta={"parser": self.name},
        )


# 注册表：后注册者优先匹配自身后缀（便于用 MinerU 等实现接管 .pdf）
_PARSERS: list[DocumentParser] = [HtmlTextParser(), PlainTextParser()]


def register_parser(parser: DocumentParser, *, prepend: bool = True) -> None:
    """注册解析器；同名解析器会被替换（幂等，lifespan 重复调用不会堆叠）。

    prepend=True 时优先于既有实现（例如用 MinerU 接管 .pdf / .docx）。
    """
    if not isinstance(parser, DocumentParser):
        raise CustomException(msg="解析器须实现 name/suffixes/parse 协议", status_code=status.HTTP_400_BAD_REQUEST)
    global _PARSERS
    _PARSERS = [item for item in _PARSERS if item.name != parser.name]
    if prepend:
        _PARSERS.insert(0, parser)
    else:
        _PARSERS.append(parser)


def registered_parser_names() -> list[str]:
    """已注册解析器名称（按匹配优先级排序），供自检与启动日志。"""
    return [parser.name for parser in _PARSERS]


def supported_suffixes() -> list[str]:
    """当前已接入的后缀清单（供接口自检/文档展示）。"""
    suffixes: list[str] = []
    for parser in _PARSERS:
        for suffix in parser.suffixes:
            if suffix not in suffixes:
                suffixes.append(suffix)
    return sorted(suffixes)


def _resolve_parser(suffix: str) -> DocumentParser:
    for parser in _PARSERS:
        if suffix in parser.suffixes:
            return parser
    raise CustomException(
        msg=(
            f"暂不支持解析 {suffix or '无后缀'} 文件；已接入：{'/'.join(supported_suffixes())}"
            "（PDF/Office/图片需启用并安装 MinerU 解析器）"
        ),
        status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
    )


def parse_document(path: str | Path, *, data: bytes | None = None) -> ParsedDocument:
    """同步解析入口：本身是阻塞调用，异步链路里请放到独立线程池执行。

    不在本层预读文件：纯文本解析器自己读，MinerU 要的是文件路径（少一次全量读盘），
    调用方手里已有字节（上传/对象存储）时再通过 ``data`` 传入。
    """
    resolved = Path(path)
    parser = _resolve_parser(resolved.suffix.lower())
    return parser.parse(resolved, data)
