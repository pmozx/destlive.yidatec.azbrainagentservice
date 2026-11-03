"""PDF 页图渲染（T2.4）：把原始 PDF 逐页光栅化为 PNG，供 `#02` 的 `result.pages` 预览。

为什么单独一个模块：
- 只依赖 ``pypdfium2``，与解析/分片链路解耦：MinerU 负责内容提取，这里只负责「看得见」的页图；
- 渲染是 CPU/内存密集的同步调用，异步链路里由调用方丢进线程池（``asyncio.to_thread``）；
- 只输出 PNG 字节，不落盘、不拼 URL——存储与有效期由业务层决定。

PNG 编码用标准库 ``zlib`` 手写，避免为一张预览图再引入 Pillow/numpy：
pdfium 的位图默认是 BGR(A) 排列，逐行去掉 alpha 后按 RGB 写入即可。
"""

import struct
import zlib
from dataclasses import dataclass


@dataclass(frozen=True)
class RenderedPage:
    """一页渲染结果：页码从 1 起，宽高为像素，``data`` 为 PNG 字节。"""

    page_no: int
    width: int
    height: int
    data: bytes


@dataclass(frozen=True)
class PdfRenderResult:
    """一次 PDF 渲染的结果与总页数（总页数用于「只渲染前 N 页」的告警）。"""

    pages: list[RenderedPage]
    total_pages: int


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    """PNG 数据块：长度 + 类型 + 内容 + CRC32。"""
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)


def _bitmap_to_png(bitmap) -> bytes:
    """pdfium 位图（BGR/BGRA）→ RGB PNG（无 alpha）。"""
    width = int(bitmap.width)
    height = int(bitmap.height)
    stride = int(bitmap.stride)
    channels = int(bitmap.n_channels)
    if channels not in (3, 4):
        raise ValueError(f"不支持的位图通道数：{channels}")
    pixels = bytes(bitmap.buffer)
    row_bytes = width * channels
    raw = bytearray()
    for y in range(height):
        start = y * stride
        row = pixels[start : start + row_bytes]
        rgb = bytearray(width * 3)
        # pdfium 默认 BGR(A)：R 在每像素第 3 个字节，G 第 2 个，B 第 1 个
        rgb[0::3] = row[2::channels]
        rgb[1::3] = row[1::channels]
        rgb[2::3] = row[0::channels]
        raw.append(0)  # PNG 行过滤类型：None
        raw.extend(rgb)
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8bit / truecolor / 非隔行
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(bytes(raw), 6))
        + _png_chunk(b"IEND", b"")
    )


def render_pdf_pages(data: bytes, *, max_pages: int, scale: float) -> PdfRenderResult:
    """把 PDF 前 ``max_pages`` 页渲染成 PNG（同步阻塞，调用方负责放线程池）。

    ``scale`` 是 pdfium 的缩放倍率（1.0 ≈ 72dpi，2.0 ≈ 144dpi）；超限页数不渲染，
    由调用方按 ``total_pages`` 产出非致命告警。
    """
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(data)
    try:
        total = len(document)
        limit = max(0, min(total, max_pages))
        pages: list[RenderedPage] = []
        for index in range(limit):
            page = document[index]
            try:
                bitmap = page.render(scale=scale)
                try:
                    pages.append(
                        RenderedPage(
                            page_no=index + 1,
                            width=int(bitmap.width),
                            height=int(bitmap.height),
                            data=_bitmap_to_png(bitmap),
                        )
                    )
                finally:
                    bitmap.close()
            finally:
                page.close()
        return PdfRenderResult(pages=pages, total_pages=total)
    finally:
        document.close()
