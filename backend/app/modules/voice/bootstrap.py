"""voice 域启动引导：表结构与元数据（8 张）。

语音质检本期只建表（接口文档未提供，实现暂缓），后续实现只写业务代码，不再动 schema。
"""

from sqlalchemy.sql.schema import Table

from app.core.table_bootstrap import create_missing_tables

from .models import (
    VoiceEvalCaseModel,
    VoiceFindingModel,
    VoiceQcJobModel,
    VoiceQcResultModel,
    VoiceReportSnapshotModel,
    VoiceReviewModel,
    VoiceRuleModel,
    VoiceRuleVersionModel,
)

TABLES: tuple[Table, ...] = (
    VoiceRuleModel.__table__,
    VoiceRuleVersionModel.__table__,
    VoiceQcJobModel.__table__,
    VoiceQcResultModel.__table__,
    VoiceReportSnapshotModel.__table__,
    VoiceFindingModel.__table__,
    VoiceReviewModel.__table__,
    VoiceEvalCaseModel.__table__,
)


async def ensure_tables() -> list[str]:
    """幂等创建语音质检域 8 张表（规则 / 版本 / 任务 / 结果 / 报告 / 发现 / 复核 / 评估集）。"""
    return await create_missing_tables(TABLES, "voice")
