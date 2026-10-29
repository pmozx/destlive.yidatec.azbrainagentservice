"""voice 模块表模型（P1 / T1.0）：规则与版本、质检任务与结果、报告、发现、复核、评估集。

语音质检本期**暂缓实现**（接口文档未提供），但 T1.0 要求 23 张表一次建模到位：
表结构先落库，后续实现只写业务代码，不再动 schema。命名 `sbp_ai_voice_*`。
"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_model import MappedBase, ModelMixin


class VoiceRuleModel(ModelMixin):
    """质检规则（表 sbp_ai_voice_rule）：`rule_code` 唯一，`version` 指向当前版本。"""

    __tablename__: str = "sbp_ai_voice_rule"
    __table_args__: tuple = (
        UniqueConstraint("rule_code", name="uq_sbp_ai_voice_rule_code"),
        Index("ix_sbp_ai_voice_rule_status_deleted", "status", "is_deleted"),
        Index("ix_sbp_ai_voice_rule_created_deleted", "created_time", "is_deleted"),
        Index("ix_sbp_ai_voice_rule_type_status", "rule_type", "status"),
        {"comment": "语音质检规则"},
    )

    rule_code: Mapped[str] = mapped_column(String(64), nullable=False, comment="规则编码")
    name: Mapped[str] = mapped_column(String(200), nullable=False, comment="规则名称（六段式之首）")
    category: Mapped[str | None] = mapped_column(String(50), default=None, nullable=True, comment="一级缺陷分类")
    rule_type: Mapped[str] = mapped_column(String(20), nullable=False, comment="hard_rule/semantic/knowledge_consistency/ticket_consistency")
    scope: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None, nullable=True, comment="适用范围")
    method: Mapped[str | None] = mapped_column(String(20), default=None, nullable=True, comment="keyword/regex/enum/field/threshold")
    params: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None, nullable=True, comment="判定参数")
    score: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="分值（正扣分/负加分）")
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False, comment="优先级（小者先判）")
    knowledge_ref: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None, nullable=True, comment="知识依据")
    examples: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False, comment="正反例")
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None, nullable=True, comment="生效开始")
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None, nullable=True, comment="生效结束")
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False, comment="当前版本")
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False, comment="draft/active/retired")


class VoiceRuleVersionModel(ModelMixin):
    """规则版本历史（表 sbp_ai_voice_rule_version）：全量快照，支持回滚。"""

    __tablename__: str = "sbp_ai_voice_rule_version"
    __table_args__: tuple = (
        UniqueConstraint("rule_id", "version", name="uq_sbp_ai_voice_rule_version_rule_ver"),
        Index("ix_sbp_ai_voice_rule_version_created_deleted", "created_time", "is_deleted"),
        {"comment": "规则版本快照"},
    )

    rule_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="规则标识（rule.uuid）")
    version: Mapped[int] = mapped_column(Integer, nullable=False, comment="版本号")
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False, comment="全量规则快照")
    changed_by: Mapped[str | None] = mapped_column(String(50), default=None, nullable=True, comment="变更人")
    remark: Mapped[str | None] = mapped_column(String(500), default=None, nullable=True, comment="变更说明")


class VoiceQcJobModel(ModelMixin):
    """质检任务（表 sbp_ai_voice_qc_job）：一通通话一个任务，`call_id` 唯一。"""

    __tablename__: str = "sbp_ai_voice_qc_job"
    __table_args__: tuple = (
        UniqueConstraint("call_id", name="uq_sbp_ai_voice_qc_job_call"),
        Index("ix_sbp_ai_voice_qc_job_status_deleted", "status", "is_deleted"),
        Index("ix_sbp_ai_voice_qc_job_created_deleted", "created_time", "is_deleted"),
        Index("ix_sbp_ai_voice_qc_job_status_created", "status", "created_time"),
        {"comment": "质检任务（四类判定并行执行）"},
    )

    call_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="通话标识（GSI 提供）")
    transcript: Mapped[str | None] = mapped_column(Text, default=None, nullable=True, comment="转写文本（最小必要保存）")
    ticket_meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None, nullable=True, comment="工单元数据")
    rule_set_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None, nullable=True, comment="本次规则集快照")
    error_code: Mapped[str | None] = mapped_column(String(64), default=None, nullable=True, comment="失败码")
    error_message: Mapped[str | None] = mapped_column(Text, default=None, nullable=True, comment="失败说明")
    status: Mapped[str] = mapped_column(String(20), default="queued", nullable=False, comment="queued/running/succeeded/failed/partial")


class VoiceQcResultModel(ModelMixin):
    """单通判定结果（表 sbp_ai_voice_qc_result）★ 核心表：结论、证据、计分、复核态。"""

    __tablename__: str = "sbp_ai_voice_qc_result"
    __table_args__: tuple = (
        UniqueConstraint("job_id", name="uq_sbp_ai_voice_qc_result_job"),
        Index("ix_sbp_ai_voice_qc_result_status_deleted", "status", "is_deleted"),
        Index("ix_sbp_ai_voice_qc_result_created_deleted", "created_time", "is_deleted"),
        Index("ix_sbp_ai_voice_qc_result_call", "call_id"),
        Index("ix_sbp_ai_voice_qc_result_review", "review_status"),
        {"comment": "单通质检结果（证据与判定强绑定 JSONB）"},
    )

    job_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="任务标识（1:1）")
    call_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="通话标识（冗余便于直查）")
    rule_results: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False, comment="逐规则结果")
    dimension_scores: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None, nullable=True, comment="维度分")
    total_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), default=None, nullable=True, comment="总分")
    defects: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False, comment="缺陷清单")
    risks: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False, comment="风险标签")
    undetermined: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False, comment="未判定清单")
    model_versions: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None, nullable=True, comment="模型与提示词版本")
    review_status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False, comment="pending/passed/corrected")
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False, comment="active 等（复核态见 review_status）")


class VoiceReportSnapshotModel(ModelMixin):
    """报告快照（表 sbp_ai_voice_report_snapshot）：数字只来自 SQL 聚合 `payload`。"""

    __tablename__: str = "sbp_ai_voice_report_snapshot"
    __table_args__: tuple = (
        UniqueConstraint("report_level", "period", "dimension", name="uq_sbp_ai_voice_report_snapshot_level_period_dim"),
        Index("ix_sbp_ai_voice_report_snapshot_status_deleted", "status", "is_deleted"),
        Index("ix_sbp_ai_voice_report_snapshot_created_deleted", "created_time", "is_deleted"),
        Index("ix_sbp_ai_voice_report_snapshot_level_created", "report_level", "created_time"),
        {"comment": "四层报告快照（数字与文字分离）"},
    )

    report_level: Mapped[str] = mapped_column(String(20), nullable=False, comment="single/employee/team/finding")
    period: Mapped[str] = mapped_column(String(32), nullable=False, comment="统计周期（如 2026-W44）")
    dimension: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False, comment="统计维度")
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False, comment="SQL 聚合数字（唯一可信数值）")
    interpretation: Mapped[str | None] = mapped_column(Text, default=None, nullable=True, comment="模型文字解读")
    checksum: Mapped[str | None] = mapped_column(String(64), default=None, nullable=True, comment="payload 哈希")
    generated_by: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None, nullable=True, comment="生成参数")
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False, comment="active 等")


class VoiceFindingModel(ModelMixin):
    """管理发现（表 sbp_ai_voice_finding）：缺陷聚类的管理动作载体。"""

    __tablename__: str = "sbp_ai_voice_finding"
    __table_args__: tuple = (
        Index("ix_sbp_ai_voice_finding_status_deleted", "status", "is_deleted"),
        Index("ix_sbp_ai_voice_finding_created_deleted", "created_time", "is_deleted"),
        Index("ix_sbp_ai_voice_finding_risk_status", "risk_level", "status"),
        {"comment": "管理发现（聚类 → 建议动作 → 跟踪）"},
    )

    title: Mapped[str] = mapped_column(String(200), nullable=False, comment="标题")
    description: Mapped[str | None] = mapped_column(Text, default=None, nullable=True, comment="描述")
    impact_scope: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None, nullable=True, comment="影响范围")
    root_cause: Mapped[str | None] = mapped_column(String(50), default=None, nullable=True, comment="根因分类")
    evidence_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False, comment="证据样本引用")
    risk_level: Mapped[str] = mapped_column(String(10), default="low", nullable=False, comment="low/medium/high")
    suggestion: Mapped[str | None] = mapped_column(Text, default=None, nullable=True, comment="建议动作")
    owner: Mapped[str | None] = mapped_column(String(50), default=None, nullable=True, comment="责任人")
    track_status: Mapped[str] = mapped_column(String(20), default="open", nullable=False, comment="跟踪状态")
    status: Mapped[str] = mapped_column(String(20), default="open", nullable=False, comment="open/tracking/closed")


class VoiceReviewModel(ModelMixin):
    """复核与申诉（表 sbp_ai_voice_review）：修正留原值，结果表只存当前生效结论。"""

    __tablename__: str = "sbp_ai_voice_review"
    __table_args__: tuple = (
        Index("ix_sbp_ai_voice_review_status_deleted", "status", "is_deleted"),
        Index("ix_sbp_ai_voice_review_created_deleted", "created_time", "is_deleted"),
        Index("ix_sbp_ai_voice_review_result", "result_id"),
        {"comment": "复核与申诉记录"},
    )

    result_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="关联判定结果")
    type: Mapped[str] = mapped_column(String(10), default="review", nullable=False, comment="review/appeal")
    submitter: Mapped[str | None] = mapped_column(String(50), default=None, nullable=True, comment="提交人")
    original_verdict: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None, nullable=True, comment="原判定")
    corrected_verdict: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None, nullable=True, comment="修正后判定")
    reason: Mapped[str | None] = mapped_column(Text, default=None, nullable=True, comment="理由")
    handled_by: Mapped[str | None] = mapped_column(String(50), default=None, nullable=True, comment="处理人")
    status: Mapped[str] = mapped_column(String(20), default="submitted", nullable=False, comment="submitted/accepted/rejected")


class VoiceEvalCaseModel(MappedBase):
    """评估集（表 sbp_ai_voice_eval_case）：人工修正样本回灌，规则与提示词调优基准。"""

    __tablename__: str = "sbp_ai_voice_eval_case"
    __table_args__: tuple = (
        Index("ix_sbp_ai_voice_eval_case_type_passed", "case_type", "passed"),
        {"comment": "评估集（回归基准）"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="主键ID")
    case_type: Mapped[str] = mapped_column(String(20), nullable=False, comment="rule/prompt")
    input: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False, comment="输入")
    expected: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None, nullable=True, comment="期望输出")
    actual: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None, nullable=True, comment="实际输出")
    passed: Mapped[bool | None] = mapped_column(Boolean, default=None, nullable=True, comment="是否通过")
    source: Mapped[str] = mapped_column(String(20), default="manual", nullable=False, comment="manual/sample/review")
    created_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False, comment="创建时间"
    )
