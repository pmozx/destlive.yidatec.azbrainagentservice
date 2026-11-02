"""``CRUDBase._build_conditions`` 的回归测试（报告 P1-6）。

坑：``BaseQueryParam`` 无条件继承 ``updated_time``，而台账类表（nonce / idempotency /
request_log / auth_cache / parse_result / eval_case）没有该列——按时间范围过滤会
``AttributeError`` → 500。正确行为是**忽略该条件**，而不是整条查询失败。
"""

from app.modules.system.nonce.crud import SystemNonceCRUD


async def test_query_param_for_missing_column_is_ignored() -> None:
    """台账表没有 ``updated_time``：传时间范围应「忽略该条件」，而不是 AttributeError → 500。"""
    crud = SystemNonceCRUD(db=None)  # type: ignore[arg-type] 只测条件构造，不触达会话

    conditions = await crud._build_conditions(
        updated_time=("between", ["2026-01-01 00:00:00", "2026-01-02 00:00:00"]),
    )

    assert conditions == []


async def test_query_param_for_existing_column_still_filters() -> None:
    """已有列不受影响：条件照常拼进去（避免"全跳过"式的过度修复）。"""
    crud = SystemNonceCRUD(db=None)  # type: ignore[arg-type]

    conditions = await crud._build_conditions(nonce="abc")

    assert len(conditions) == 1
