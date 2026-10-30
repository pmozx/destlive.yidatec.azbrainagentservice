"""MinerU 解析器：PDF / Office / 图片走 MinerU 做版面还原，产出 Markdown 作为分片输入。

为什么是同步实现：``DocumentParser.parse`` 本来就在解析线程池里执行，MinerU 的
“提交 + 轮询”（分钟级）会一直占住该线程——这正是独立线程池的用途，无需再包协程。

MinerU 只接受“文件路径或 URL”：调用方给的是字节（上传流/对象存储）时，这里写临时文件
中转并保留原文件名（MinerU 依据后缀推断模型），解析结束立即清理。
"""

import shutil
import tempfile
from pathlib import Path

from app.config.setting import settings
from app.core.clients.mineru import MineruClient, MineruDocument
from app.core.logger import logger
from app.utils.page_locator import build_page_map

from .parsers import ParsedDocument, ParsedImage, register_parser

# MinerU 支持且本地内置解析器不覆盖的类型（.txt/.md/.html 仍走本地，省一次上传）
MINERU_SUFFIXES: tuple[str, ...] = (
    ".pdf",
    ".doc",
    ".docx",
    ".ppt",
    ".pptx",
    ".xls",
    ".xlsx",
    ".png",
    ".jpg",
    ".jpeg",
    ".bmp",
    ".tif",
    ".tiff",
    ".webp",
)


class MineruDocumentParser:
    """MinerU 解析器（二进制/版面类文档的内容提取主路径）。"""

    name = "mineru"
    suffixes: tuple[str, ...] = MINERU_SUFFIXES

    def __init__(self, client: MineruClient | None = None) -> None:
        self._client = client

    def _extract(self, path: Path, data: bytes | None) -> MineruDocument:
        client = self._client or MineruClient()
        if data is None:
            return client.extract(path)

        tmp_dir = Path(tempfile.mkdtemp(prefix="sbp_mineru_"))
        try:
            # 保留原文件名：MinerU 依据后缀选模型，改名成 tmp.bin 会直接判为不支持的类型
            upload = tmp_dir / (path.name or f"document{path.suffix or '.pdf'}")
            upload.write_bytes(data)
            return client.extract(upload)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def parse(self, path: Path, data: bytes | None) -> ParsedDocument:
        result = self._extract(path, data)
        return ParsedDocument(
            text=result.markdown,
            source=path.name,
            suffix=path.suffix.lower(),
            meta={
                "parser": self.name,
                "mode": result.mode,
                "model": result.model,
                "task_id": result.task_id,
                "pages": result.page_count,
                "parse_elapsed_ms": result.elapsed_ms,
            },
            page_map=build_page_map(result.content_list),
            # 版面 JSON 与图片只在精准解析（有 token）时才有；flash 模式两者皆空
            content_list=list(result.content_list),
            images=[ParsedImage(name=image.name, data=image.data) for image in result.images],
        )


def install_mineru_parser(parser: MineruDocumentParser | None = None) -> MineruDocumentParser | None:
    """按配置安装 MinerU 解析器（MINERU_ENABLE=true 时接管二进制文档后缀）。

    幂等：同名解析器重复注册会被替换，不会在注册表里堆叠。
    """
    if not settings.MINERU_ENABLE:
        logger.info("==== MinerU 解析未启用（MINERU_ENABLE=false），PDF/Office/图片将提示「暂不支持解析」 ====")
        return None

    installed = parser or MineruDocumentParser()
    register_parser(installed, prepend=True)
    logger.info(
        "==== MinerU 解析已接管 {}（模式 {}，模型 {}）====",
        "/".join(installed.suffixes),
        MineruClient().mode,
        settings.MINERU_MODEL,
    )
    return installed
