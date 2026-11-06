"""问答指标 Schema：管理端只读投影（单轮明细 + 会话维度汇总）。

指标是**事实快照**（一轮一行、只写不改），管理端只读，不提供任何写入口：

- 明细按 ``session_id`` 取，顺序与对话流一致（``created_time`` 升序）；
- 汇总按**会话维度**过滤后聚合（会话 ID / 用户 / 标题 / 状态），与列表页同一套条件。
  指标行自身的时间范围刻意不参与——页面上的「这批会话」问的是会话什么时候建的，
  而不是这一轮什么时候跑的；两个时间轴混用会让概览卡与列表对不上。

两个口径必须先说清，否则页面看起来像算错了：

1. **轮数 ≠ 消息数 / 2**：失败轮（断连、超时、生成失败）只有指标没有助手消息，
   清空历史后消息被置 ``deleted`` 而指标不动。
2. **有消息也可能缺指标**：指标写失败只告警（观测数据不能把一次正常问答变成错误帧），
   页面必须允许「有回答、无指标」这一状态。
"""

from dataclasses import dataclass

from pydantic import BaseModel, Field

from app.core.validator import DateTimeStr

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# §一  领域层 dataclass（CRUD 聚合结果 → 管理端服务的中间形状）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@dataclass(frozen=True)
class TurnSummary:
    """一组会话的指标聚合（空集时除 ``turns`` 外均为 0 / None）。"""

    turns: int = 0
    no_evidence_turns: int = 0
    failed_turns: int = 0
    avg_total_ms: int | None = None
    avg_first_token_ms: int | None = None
    tokens_in: int = 0
    tokens_out: int = 0


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# §二  管理端 Pydantic Schema（/kb/chat-metric）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class KbChatTurnMetricOutSchema(BaseModel):
    """单轮指标明细。

    不继承 ``BaseSchema``：本表没有 ``uuid`` / ``updated_time`` 列，继承只会让每行恒定多出
    两个 ``null``。``created_time`` 用 ``DateTimeStr``，与其它管理端出口同一条时间口径。
    """

    id: int = Field(..., description="主键ID")
    created_time: DateTimeStr | None = Field(default=None, description="本轮发生时间")
    session_id: str = Field(default="", description="归属会话 ID")
    assistant_message_id: str | None = Field(default=None, description="助手消息 uuid（失败轮为空）")
    trace_id: str | None = Field(default=None, description="LangSmith trace id（未开启追踪为空）")
    mode: str = Field(default="", description="sync/stream")
    model: str = Field(default="", description="本轮使用的模型名快照")
    prompt_hash: str | None = Field(default=None, description="业务提示词指纹")
    evidence_count: int = Field(default=0, description="进入上下文的证据条数")
    top_score: float | None = Field(default=None, description="最高相关度（排序质量）")
    tokens_in: int = Field(default=0, description="输入 token")
    tokens_out: int = Field(default=0, description="输出 token")
    retrieve_ms: int = Field(default=0, description="检索+回查耗时（毫秒）")
    first_token_ms: int | None = Field(default=None, description="首字耗时（毫秒，仅流式）")
    generate_ms: int = Field(default=0, description="生成耗时（毫秒）")
    persist_ms: int = Field(default=0, description="落库耗时（毫秒）")
    total_ms: int = Field(default=0, description="整轮耗时（毫秒）")
    no_evidence: bool = Field(default=False, description="是否因证据不足拒答")
    finish_reason: str | None = Field(default=None, description="stop/no_evidence")
    invalid_citations: int = Field(default=0, description="被摘除的越界引用编号个数")
    error_code: str | None = Field(default=None, description="失败码（成功轮为空）")


class KbChatTurnMetricSummaryOutSchema(BaseModel):
    """会话维度汇总（概览卡口径）：比率在后端算，页面不再各算一遍。

    ``no_evidence_rate`` 是**拒答轮 / 总轮数**，不是「有证据轮的平均分」；
    ``avg_first_token_ms`` 只在流式轮有值，非流式轮为 ``None`` 不参与平均。
    """

    turns: int = Field(default=0, description="总轮数")
    no_evidence_turns: int = Field(default=0, description="因无证据拒答的轮数")
    no_evidence_rate: float = Field(default=0.0, description="拒答率（0–1）")
    failed_turns: int = Field(default=0, description="失败轮数（error_code 非空）")
    avg_total_ms: int | None = Field(default=None, description="平均整轮耗时（毫秒）")
    avg_first_token_ms: int | None = Field(default=None, description="平均首字耗时（毫秒，仅流式）")
    tokens_in: int = Field(default=0, description="输入 token 合计")
    tokens_out: int = Field(default=0, description="输出 token 合计")


class KbChatTurnMetricQueryParam(BaseModel):
    """明细查询参数：按会话看每一轮（空会话也是一条合法查询，返回空页）。"""

    session_id: str = Field(..., min_length=1, max_length=64, description="会话 ID（精确匹配）")


class KbChatTurnMetricSummaryQueryParam(BaseModel):
    """汇总查询参数：与「会话记录」列表页的筛选条件同口径。

    刻意不继承 ``BaseQueryParam``：继承会白送一个 ``created_time`` 时间范围，而这里的过滤
    落在**会话表**上，页面传「今天」却按会话创建时间筛轮次，是最容易被误读的一种口径。
    """

    session_id: str | None = Field(None, max_length=64, description="会话 ID（模糊匹配）")
    user_id: int | None = Field(None, description="归属用户主键（sbp_sys_user.id）")
    title: str | None = Field(None, max_length=100, description="会话标题（模糊匹配）")
    status: str | None = Field(None, description="会话状态 active/cleared/deleted")
