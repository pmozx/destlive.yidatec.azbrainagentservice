"""`/v1` 重复治理接口（#16 保存前查重 / #17 分组查询 / #18 分组详情 / #19 合并预览）。

四件事都走 ``make_contract_router``：验签、授权时效、审计与错误体口径与其它接口一致；
业务编排在 ``service.py``，检测与存储全部落在 ``app.modules.kb.dup_result``。

**路由声明顺序**：``/duplicates/groups/query`` 必须写在 ``/duplicates/groups/{group_id}/query``
之前，否则 Starlette 会先用路径参数匹配到 ``group_id="query"``，把分页查询变成 404。
"""

from typing import Any

from ..common import make_contract_router
from .schema import (
    DuplicateCheckRequest,
    DuplicateGroupQueryRequest,
    DuplicateGroupsQueryRequest,
    MergePreviewRequest,
)
from .service import (
    check_duplicates,
    generate_merge_preview,
    query_duplicate_group_detail,
    query_duplicate_groups,
)

DuplicateRouter = make_contract_router(prefix="/knowledge", tags=["契约 /v1 重复治理（#16–#19）"])


@DuplicateRouter.post("/duplicates/check", summary="#16 保存前重复检查")
async def duplicate_check(payload: DuplicateCheckRequest) -> dict[str, Any]:
    """T6.1：只比较授权范围内的内容，返回命中项；达到阈值才 ``is_duplicate=true``。

    失败一律显式报错（403 越权 / 409 来源变化），不返回「未发现重复」。
    """
    return await check_duplicates(payload)


@DuplicateRouter.post("/duplicates/groups/query", summary="#17 查询重复分组")
async def duplicate_groups(payload: DuplicateGroupsQueryRequest) -> dict[str, Any]:
    """T6.2：首查全量检测并落 30 分钟只读结果，翻页带 ``result_id`` 复用同一份结果。"""
    return await query_duplicate_groups(payload)


@DuplicateRouter.post("/duplicates/groups/{group_id}/query", summary="#18 分组详情与对比依据")
async def duplicate_group_detail(group_id: str, payload: DuplicateGroupQueryRequest) -> dict[str, Any]:
    """T6.2：组内成员全文 + 成对证据；成员被修订或越权 → 409 / 403。"""
    return await query_duplicate_group_detail(group_id, payload)


@DuplicateRouter.post("/duplicates/merge-preview", summary="#19 合并预览（不落正式库）")
async def duplicate_merge_preview(payload: MergePreviewRequest) -> dict[str, Any]:
    """T6.3：返回 ``preview_id`` / ``preview_hash`` 与冲突、告警清单；写正式库由 GSI 审批流负责。"""
    return await generate_merge_preview(payload)
