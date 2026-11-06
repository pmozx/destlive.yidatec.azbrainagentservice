"""一轮问答的度量采集与落库（表 ``sbp_ai_kb_chat_turn_metric``）。

分工：trace 回答「这一轮发生了什么」（节点顺序、每步耗时、模型输入输出的调试视图），
本模块回答「这一周质量与成本怎么变化」（可 SQL 聚合、不依赖外部 SaaS）。

三条约束：

1. **只写不改**：指标是事实快照，不做更新；晚到的补偿没有意义（耗时已经过去了）；
2. **写失败即降级**：只告警，绝不能让观测数据把一次正常问答变成错误帧——调用方 await 它，
   但异常在内部吞掉；
3. **不存正文**：问题、切片、回答都不进这张表（正文在消息表里），这里只存哈希、条数、
   耗时与 token，避免同一份敏感文本在库里出现两处。
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from hashlib import sha256
from time import perf_counter

from app.core.database import async_session_factory
from app.core.logger import logger
from app.core.observability import current_trace_id, trace_metadata, user_pseudonym

from .model import KbChatTurnMetricModel

MODE_SYNC = "sync"
MODE_STREAM = "stream"

#: 指标专用失败码：客户端断开不产生 SSE 错误帧（没人接收），但必须记下来
ERR_CLIENT_DISCONNECTED = "CLIENT_DISCONNECTED"


@dataclass(slots=True)
class TurnMeter:
    """单轮度量累加器：各步骤往里写，收尾时一次落库。

    ``started`` 在构造时记下（不是第一次检索时）：排队与快照查询也该算进整轮耗时，
    否则「为什么慢」会被几段等待时间摊薄掉。
    """

    session_id: str
    tenant_id: str
    user_id: int
    mode: str
    model: str = ""
    prompt_hash: str | None = None
    evidence_count: int = 0
    top_score: float | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    retrieve_ms: int = 0
    first_token_ms: int | None = None
    generate_ms: int = 0
    persist_ms: int = 0
    invalid_citations: int = 0
    assistant_message_id: str | None = None
    started: float = field(default_factory=perf_counter, repr=False)

    def prompt(self, prompt_text: str) -> None:
        """记业务提示词指纹：只存哈希，换提示词后按它分组对比效果。"""
        self.prompt_hash = sha256(prompt_text.strip().encode("utf-8")).hexdigest()[:16]

    def annotate(self) -> None:
        """把本轮口径写到当前 trace：会话/模式/模型/用户伪名与提示词指纹，不含正文。"""
        trace_metadata(
            session_id=self.session_id,
            tenant_id=self.tenant_id,
            mode=self.mode,
            model=self.model or None,
            user_hash=user_pseudonym(self.user_id),
            prompt_hash=self.prompt_hash,
        )

    def evidences(self, scores: Sequence[float]) -> None:
        """记证据条数与最高相关度（排序质量：有证据但 top1 很低，说明召回勉强）。"""
        self.evidence_count = len(scores)
        self.top_score = max(scores) if scores else None

    async def save(
        self,
        *,
        finish_reason: str | None = None,
        no_evidence: bool = False,
        error_code: str | None = None,
    ) -> None:
        """落库一条指标；异常只在内部告警。

        不用 ``CRUDBase.create``：它会 ``refresh`` 回读一次，而这里的写入方永远不看返回值，
        多一次往返只会让 SSE 收尾多等几毫秒。
        """
        try:
            async with async_session_factory() as db, db.begin():
                db.add(
                    KbChatTurnMetricModel(
                        trace_id=current_trace_id(),
                        session_id=self.session_id,
                        assistant_message_id=self.assistant_message_id,
                        tenant_id=self.tenant_id,
                        user_hash=user_pseudonym(self.user_id),
                        mode=self.mode,
                        model=self.model,
                        prompt_hash=self.prompt_hash,
                        evidence_count=self.evidence_count,
                        top_score=self.top_score,
                        tokens_in=self.tokens_in,
                        tokens_out=self.tokens_out,
                        retrieve_ms=self.retrieve_ms,
                        first_token_ms=self.first_token_ms,
                        generate_ms=self.generate_ms,
                        persist_ms=self.persist_ms,
                        total_ms=round((perf_counter() - self.started) * 1000),
                        no_evidence=no_evidence,
                        finish_reason=finish_reason,
                        invalid_citations=self.invalid_citations,
                        error_code=error_code,
                    )
                )
        except Exception as exc:  # noqa: BLE001 观测数据写失败不能影响问答结果
            logger.warning("问答指标写入失败（不影响本轮问答）：session={} {}", self.session_id, exc)

    async def fail(self, error_code: str) -> None:
        """记一条失败轮：没有助手消息与引用，只留失败码、耗时与已经发生的用量。"""
        await self.save(error_code=error_code)
