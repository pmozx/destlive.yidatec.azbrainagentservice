"""页码映射：把解析服务返回的 ``content_list``（版面块 + page_idx）兑换成「分片 → 页码」。

为什么不能按块下标对齐：分片是在 Markdown 上重新拼装并做重叠的，与版面块不是一一对应
（一个版面块可能被拆进两个分片，多个版面块也可能并进一个分片），只能靠文本反查。

定位策略（按可靠程度降序，命中即返回）：
1. 分片开头文本出现在某个版面块内部：分片起点落在该块里，最可靠；
2. 版面块文本出现在分片开头窗口内：分片以标题行起头时的典型命中；
3. 都命不中则沿用上一个分片的页码：分片按文档顺序生成，相邻块页码几乎不会跨页，
   这比留空更有用（页码留空会让「按页码筛选」直接筛不到任何数据）。

游标单调前进：从上一个命中块的下标往后找，避免页眉/重复标题把后面的分片拉回第 1 页。

前提：页码来自带版面块的精准解析结果。只有纯文本/Markdown 结果时拿不到页码，
本模块返回空映射，属预期行为。
"""

import re

# Markdown 标记字符：定位前先抹掉，避免标题符号/表格竖线挡住首句
_MARKDOWN_NOISE = re.compile(r"[#*`>\[\]()!|_~-]+")
# 探针长度（字符）：太短容易误命中，太长则 Markdown 改写后匹配不上
_PROBE_LEN = 40
# 探针最短长度：不足该长度直接放弃精确定位（改走「沿用上一块」）
_MIN_PROBE_LEN = 8
# 「块文本出现在分片里」的比对窗口：只看分片开头这么多字符，避免短块命中远端内容
_WINDOW_LEN = 160


def _normalize(text: str) -> str:
    """统一口径：抹掉 Markdown 标记、折叠空白、转小写（全角/半角差异留给使用者）。"""
    return " ".join(_MARKDOWN_NOISE.sub(" ", (text or "").replace("\u3000", " ")).split()).lower()


def build_page_map(content_list: list[dict] | None) -> list[tuple[str, int]]:
    """从 content_list 提取 (块文本, page_idx)，只保留同时具备文本与页码的项。"""
    pairs: list[tuple[str, int]] = []
    for item in content_list or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        page_idx = item.get("page_idx")
        if text and isinstance(page_idx, int):
            pairs.append((text, page_idx))
    return pairs


class PageLocator:
    """按文档顺序把分片映射到页码的有状态定位器（一个文档一个实例）。

    必须复用同一实例逐块调用：内部游标依赖「分片按顺序生成」这一前提，
    单独对每个分片新建实例会退化成全篇重扫，页眉也能把页码拉回首页。
    """

    def __init__(self, page_map: list[tuple[str, int]] | None = None, *, probe_len: int = _PROBE_LEN) -> None:
        self._blocks: list[tuple[str, int]] = []
        for text, page_idx in page_map or []:
            normalized = _normalize(text)
            if normalized:
                self._blocks.append((normalized, page_idx + 1))
        self._probe_len = max(_MIN_PROBE_LEN, probe_len)
        self._cursor = 0
        self._last: int | None = None

    @property
    def usable(self) -> bool:
        """是否具备定位能力（无版面块时恒为 False）。"""
        return bool(self._blocks)

    def _search(self, predicate, *, start: int) -> tuple[int, int] | None:
        """从 start 起找第一个满足条件的块，返回 (下标, 页码)。"""
        for index in range(max(0, start), len(self._blocks)):
            block, page_no = self._blocks[index]
            if predicate(block):
                return index, page_no
        return None

    def _locate_forward(self, predicate) -> tuple[int, int] | None:
        """先按游标向前找，找不到再全篇兜底（重叠区会把分片起点拉回上一块）。"""
        hit = self._search(predicate, start=self._cursor)
        if hit is None:
            hit = self._search(predicate, start=0)
        return hit

    def locate(self, content: str) -> int | None:
        """返回分片页码（1 起计）；定位不到时沿用上一个分片的页码。"""
        if not self._blocks:
            return None

        normalized = _normalize(content)
        if not normalized:
            return self._last

        head = normalized[: self._probe_len]
        if len(head) >= _MIN_PROBE_LEN:
            hit = self._locate_forward(lambda block: head in block)
            if hit is None:
                # 分片以标题/短块起头：块文本被完整包在分片开头窗口里
                window = normalized[:_WINDOW_LEN]
                hit = self._locate_forward(lambda block: len(block) >= _MIN_PROBE_LEN and block in window)
            if hit is None:
                # 兜底：短块出现在分片任意位置（长块不会命中，避免整段文本被当成页码依据）
                hit = self._locate_forward(lambda block: _MIN_PROBE_LEN <= len(block) <= _WINDOW_LEN and block in normalized)
            if hit is not None:
                self._cursor, page_no = hit
                self._last = page_no
                return page_no

        return self._last


def resolve_page_no(content: str, page_map: list[tuple[str, int]] | None, *, probe_len: int = _PROBE_LEN) -> int | None:
    """单块页码解析（一次性调用）。

    批量场景请改用 :class:`PageLocator` 逐块复用实例：单调游标与「沿用上一块」
    都依赖调用顺序，逐块新建实例会丢掉这两项能力。
    """
    locator = PageLocator(page_map, probe_len=probe_len)
    return locator.locate(content)
