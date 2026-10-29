"""阶段耗时采集（T0.4「关键耗时初测」的数据源）。

为什么单独抽一层：
1. POC 要回答的是「一次解析/检索/生成各阶段各花多久、60 秒超时够不够」（计划 §三 硬前提 4），
   所以耗时必须**按阶段**落在库里，而不是只打一行总耗时日志；
2. 采集失败绝不能影响业务：`record_metric` 内部已吞异常，这里只负责测量。

阶段命名在调用方约定（见 `parser.py` / `router.py`）：
`fetch` 拉文件、`parse` 解析、`chunk` 分片、`embed` 向量化、`persist` 落库、
`keyword`/`vector`/`fuse` 检索、`prompt` 拼上下文、`llm` 生成。
"""

import time
from contextlib import asynccontextmanager
from typing import AsyncIterator, Final

# 单阶段耗时上限：超过这个值在记录里标红（POC 结论用来判断是否要调参）
SLOW_STAGE_MS: Final[int] = 5_000


class StageTimer:
    """一次请求的阶段计时器：可重复 `stage()` 累加同名阶段（如多次 batch 向量化）。"""

    def __init__(self) -> None:
        self._start = time.perf_counter()
        self.stages: dict[str, int] = {}

    def record(self, name: str, seconds: float) -> None:
        """累加某阶段耗时（同阶段多次记录取合计）。"""
        self.stages[name] = self.stages.get(name, 0) + max(0, int(seconds * 1000))

    @asynccontextmanager
    async def stage(self, name: str) -> AsyncIterator[None]:
        """`async with timer.stage("embed"):` —— 异常路径也照常计时。"""
        start = time.perf_counter()
        try:
            yield
        finally:
            self.record(name, time.perf_counter() - start)

    @property
    def total_ms(self) -> int:
        return max(0, int((time.perf_counter() - self._start) * 1000))

    def snapshot(self) -> dict[str, object]:
        """落库用的明细：阶段耗时 + 超出告警线的阶段清单。"""
        return {
            "stages": dict(self.stages),
            "slow_stages": sorted(name for name, value in self.stages.items() if value >= SLOW_STAGE_MS),
        }
