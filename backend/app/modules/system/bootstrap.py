"""system 域启动引导：表结构与元数据（一表一目录，DDL 统一收敛在此）。

导入模型即把 6 张表注册进 ``MappedBase.metadata``（``models.py`` 覆盖 5 张台账，审计表
单独导入），启动期建表与测试都依赖这一点。建表口径见 ``app.core.table_bootstrap``。

除建表外，本文件还负责一处**列类型就地转换**：审计表的 ``request_payload`` / ``response_json``
从 ``TEXT`` 改为 ``JSONB``（v1.8）。``create_missing_tables`` 对已存在的表一律跳过，不改类型，
不在这里补的话老库会按 TEXT 接收 dict 而写入失败。
"""

from sqlalchemy import text
from sqlalchemy.sql.schema import Table

from app.config.setting import settings
from app.core.database import async_engine
from app.core.logger import logger
from app.core.table_bootstrap import create_missing_tables

from .api_log.model import SystemApiLogModel
from .models import (
    SystemAuthCacheModel,
    SystemIdempotencyModel,
    SystemNonceModel,
    SystemPromptTemplateModel,
    SystemRequestLogModel,
)

# 审计表受 API_LOG_AUTO_CREATE 开关控制（关闭表示由运维手工建表）；其余 5 张技术台账无条件建
TABLES: tuple[Table, ...] = (
    *((SystemApiLogModel.__table__,) if settings.API_LOG_AUTO_CREATE else ()),
    SystemAuthCacheModel.__table__,
    SystemIdempotencyModel.__table__,
    SystemNonceModel.__table__,
    SystemPromptTemplateModel.__table__,
    SystemRequestLogModel.__table__,
)

#: v1.8 起这两列存结构化 JSON（JSONB），不再是序列化后的字符串
_JSON_PAYLOAD_COLUMNS: tuple[str, ...] = ("request_payload", "response_json")

#: 容错转换函数：老值里既有正常 JSON，也有被截断/非 JSON 的历史串，直接用 ``::jsonb`` 会整列失败。
#: 挂在 ``pg_temp`` 上，只在本连接会话可见，用完随连接释放（迁移不需要在库里留函数）。
_TRY_JSONB_FN = """
CREATE OR REPLACE FUNCTION pg_temp.try_jsonb(raw text) RETURNS jsonb
LANGUAGE plpgsql AS $$
BEGIN
    RETURN raw::jsonb;
EXCEPTION WHEN others THEN
    RETURN NULL;
END;
$$
"""

#: 转换失败的历史值包成 ``{"_raw": "<原文>"}``：既不丢内容，也让列类型统一成 JSONB
_ALTER_COLUMN_TO_JSONB = """
ALTER TABLE sbp_ai_system_api_log
    ALTER COLUMN {column} TYPE JSONB
    USING CASE
              WHEN {column} IS NULL THEN NULL
              ELSE COALESCE(pg_temp.try_jsonb({column}), jsonb_build_object('_raw', {column}))
          END
"""


async def _ensure_json_payload_columns() -> None:
    """把审计表的两列载荷就地转成 JSONB（已转换过的库不做任何事）。"""
    try:
        async with async_engine.begin() as conn:
            rows = await conn.execute(
                text(
                    "SELECT column_name, data_type FROM information_schema.columns "
                    "WHERE table_schema = current_schema() AND table_name = :table"
                ),
                {"table": SystemApiLogModel.__tablename__},
            )
            current: dict[str, str] = {name: dtype for name, dtype in rows}
            # 表还不存在（新库）或已经是 jsonb：都无事可做，交给建表 / 保持原样
            pending = [column for column in _JSON_PAYLOAD_COLUMNS if current.get(column) not in (None, "jsonb")]
            if not pending:
                return
            await conn.execute(text(_TRY_JSONB_FN))
            for column in pending:
                await conn.execute(text(_ALTER_COLUMN_TO_JSONB.format(column=column)))
    except Exception as e:  # noqa: BLE001 类型没转成功只影响审计写入，不阻断启动
        logger.warning("⚠️ 审计表载荷列转 JSONB 失败（新审计写入会失败，接口本身不受影响）: {}", e)
        return
    logger.info("==== 审计表载荷列已转为 JSONB（{}）====", ", ".join(pending))


async def ensure_tables() -> list[str]:
    """幂等创建系统域表（审计日志 / 鉴权缓存 / 幂等 / nonce / 提示词 / 请求日志）并校正载荷列类型。"""
    created = await create_missing_tables(TABLES, "system")
    await _ensure_json_payload_columns()
    return created
