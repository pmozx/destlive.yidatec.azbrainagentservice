"""重复治理契约服务（#16–#19 / D06）：请求投影 → 领域编排 → 契约投影与错误码映射。

请求投影与容量校验已下沉到 ``app.modules.kb.dup_result``（``ScopeData.from_payload`` /
``GovernanceItem.from_payload``，容量取 ``settings.DUP_MAX_SCOPE_ITEMS``），检测算法与存储本来就在
那里。本层只剩三件事：

1. **错误码映射**：领域异常 → 契约码（400/403/404/409/413），领域层不感知 HTTP；
2. **业务数据补齐**：``items[].subtitle`` 需要 GSI 的受众团队（``modules_gsi``），
   两个领域包不互相 import，只在契约层汇合；
3. **响应投影**：``to_payload`` 与 ``subtitle`` 的插入位置。

``subtitle`` 是可选的展示字段：GSI 表读失败只丢副标题并记日志，**不能让治理请求失败**——
治理范围与内容一致性已经由领域层保证，展示信息缺失不影响“有没有重复”的结论。
"""

from typing import Any

from app.core.database import async_session_factory
from app.core.logger import logger
from app.modules.kb.dup_result.errors import (
    DupInvalidRequestError,
    DupResultExpiredError,
    DupResultNotFoundError,
    DupResultTooLargeError,
    DupScopeDeniedError,
    DupScopeTooLargeError,
    DupSourceChangedError,
)
from app.modules.kb.dup_result.schema import (
    ChunkCheckData,
    DuplicateCheckData,
    DuplicateGroup,
    DuplicateGroupQuery,
    DuplicateGroupsQuery,
    DuplicateHit,
    GovernanceItem,
    MergeActionData,
    MergePreviewData,
    ScopeData,
    SpotResource,
)
from app.modules.kb.dup_result.service import KbDupResultService
from app.modules_gsi.km.doc_publication.schema import SbpKmDocPublicationBrief, publication_subtitle
from app.modules_gsi.km.doc_publication.service import SbpKmDocPublicationService

from ...base.errors import (
    GOVERNANCE_SCOPE_DENIED,
    INTERNAL_ERROR,
    INVALID_REQUEST,
    NOT_FOUND,
    PAYLOAD_TOO_LARGE,
    RESULT_EXPIRED,
    RESULT_TOO_LARGE,
    SOURCE_CHANGED,
    ContractApiError,
)
from .schema import (
    ChunkCheckRequest,
    DuplicateCheckRequest,
    DuplicateGroupQueryRequest,
    DuplicateGroupsQueryRequest,
    MergePreviewRequest,
)

_SERVICE = KbDupResultService()


# ── 游标与错误码映射 ─────────────────────────────────────────────────


def _after_cursor(cursor: str | None) -> int:
    """#17 游标由本侧签发（上一页末尾的偏移量），非法游标 400 而不是从头再来。"""
    if cursor is None:
        return 0
    try:
        value = int(cursor)
    except (TypeError, ValueError) as exc:
        raise ContractApiError(INVALID_REQUEST, "cursor 无效：请原样回传上一页的 next_cursor。") from exc
    if value < 0:
        raise ContractApiError(INVALID_REQUEST, "cursor 无效：请原样回传上一页的 next_cursor。")
    return value


def _map_domain_error(exc: Exception) -> ContractApiError:
    """领域异常 → 契约错误码（§九）：只在契约层做映射，领域层不感知 HTTP。"""
    if isinstance(exc, DupScopeTooLargeError):
        # 容量超限：契约要求 413 且不静默截断，不能退化成 400
        return ContractApiError(PAYLOAD_TOO_LARGE, str(exc))
    if isinstance(exc, DupScopeDeniedError):
        return ContractApiError(GOVERNANCE_SCOPE_DENIED, str(exc))
    if isinstance(exc, DupResultTooLargeError):
        # D06 命中证据超限：契约要求 413 RESULT_TOO_LARGE，不能用截断后的 200 冒充完整结果
        return ContractApiError(RESULT_TOO_LARGE, str(exc))
    if isinstance(exc, DupSourceChangedError):
        return ContractApiError(SOURCE_CHANGED, str(exc))
    if isinstance(exc, DupResultExpiredError):
        return ContractApiError(RESULT_EXPIRED, str(exc))
    if isinstance(exc, DupResultNotFoundError):
        return ContractApiError(NOT_FOUND, str(exc))
    if isinstance(exc, DupInvalidRequestError):
        return ContractApiError(INVALID_REQUEST, str(exc))
    logger.exception("重复治理处理失败：{}", exc)
    return ContractApiError(INTERNAL_ERROR)


# ── 业务数据补齐（subtitle） ─────────────────────────────────────────


def _publication_ids(items: list[DuplicateHit] | tuple[DuplicateHit, ...]) -> list[str]:
    """收集需要查团队名的发布单元（去重保序，空 ID 不查）。"""
    seen: list[str] = []
    for item in items:
        for publication_id in item.resource.publication_ids:
            if publication_id and publication_id not in seen:
                seen.append(publication_id)
    return seen


async def _briefs(items: list[DuplicateHit] | tuple[DuplicateHit, ...]) -> dict[str, SbpKmDocPublicationBrief]:
    """读 GSI 发布单元展示信息；失败只降级掉副标题并记日志（见模块 docstring）。"""
    publication_ids = _publication_ids(items)
    if not publication_ids:
        return {}
    try:
        async with async_session_factory() as session:
            return await SbpKmDocPublicationService(session).map_briefs(publication_ids)
    except Exception as exc:
        logger.warning("重复治理：读取 GSI 发布单元展示信息失败，本次省略 subtitle：{}", exc)
        return {}


def _subtitle_of(item: DuplicateHit, briefs: dict[str, SbpKmDocPublicationBrief]) -> str | None:
    """一条命中的副标题：把该内容全部发布单元的团队名合并去重（口径见 ``publication_subtitle``）。

    返回 ``None`` 表示「查不到任何发布单元」，此时省略字段，而不是编一个「正式知识」——
    GSI 表里没有这条发布单元是业务事实，不能靠默认文案掩盖。
    """
    teams: list[str] = []
    matched = False
    for publication_id in item.resource.publication_ids:
        brief = briefs.get(publication_id)
        if brief is None:
            continue
        matched = True
        for team in brief.teams:
            if team and team not in teams:
                teams.append(team)
    return publication_subtitle(teams) if matched else None


def _hit_payload(
    hit: DuplicateHit,
    briefs: dict[str, SbpKmDocPublicationBrief],
    *,
    with_full_text: bool,
) -> dict[str, Any]:
    """命中投影；``subtitle`` 紧跟 ``title`` 插入，便于人工核对响应体。"""
    payload = hit.to_payload(with_full_text=with_full_text)
    subtitle = _subtitle_of(hit, briefs)
    if subtitle is None:
        return payload
    ordered: dict[str, Any] = {}
    for key, value in payload.items():
        ordered[key] = value
        if key == "title":
            ordered["subtitle"] = subtitle
    return ordered


def _group_payload(
    group: DuplicateGroup,
    briefs: dict[str, SbpKmDocPublicationBrief],
    *,
    with_full_text: bool,
) -> dict[str, Any]:
    payload = group.to_payload(with_full_text=with_full_text)
    payload["items"] = [_hit_payload(item, briefs, with_full_text=with_full_text) for item in group.items]
    return payload


# ── #16 保存前查重 ───────────────────────────────────────────────────


async def check_duplicates(payload: DuplicateCheckRequest) -> dict[str, Any]:
    """D01：候选内容 vs 授权范围，只报命中，不写任何表、不阻止保存。"""
    try:
        data = DuplicateCheckData(
            request_id=payload.request_id,
            tenant_id=payload.tenant_id,
            user_id=payload.user_id,
            candidate=GovernanceItem.from_payload(payload.candidate),
            scope=ScopeData.from_payload(payload.scope),
            score_profile=payload.score_profile,
            kinds=tuple(payload.kinds),
        )
        outcome = await _SERVICE.check(data)
    except Exception as exc:
        raise _map_domain_error(exc) from exc
    briefs = await _briefs(outcome.matches)
    return {
        "request_id": outcome.request_id,
        "is_duplicate": outcome.is_duplicate,
        "similarity": outcome.similarity,
        "score_profile": outcome.score_profile,
        "matches": [_hit_payload(item, briefs, with_full_text=False) for item in outcome.matches],
    }


# ── D06 单篇知识切片查重 ──────────────────────────────────


async def check_chunk_duplicates(payload: ChunkCheckRequest) -> dict[str, Any]:
    """D06：当前知识 vs 授权范围内其余知识，按切片给出命中证据与重复占比。

    与 D01 的差异是「比什么」：D01 看整篇是否重复、只报命中项；这里逐对切片算相似度，
    ``duplicate_chunks`` 按 ``source_seq`` 去重（同一片命中多篇只算一片），
    所以 GSI 能直接回答「这篇有几成片段是别处已有的内容」。

    不写任何表：结果只对本次 scope 成立，scope 时限 120 秒，没有可复用的快照。
    同样也不占幂等键：纯读计算，同 request_id + 同正文天然得到同一结果，
    存一份大体积回执进幂等台账没有收益（与 D01 一致）。
    """
    try:
        data = ChunkCheckData(
            request_id=payload.request_id,
            tenant_id=payload.tenant_id,
            user_id=payload.user_id,
            candidate=SpotResource.from_payload(payload.candidate_resource),
            scope=ScopeData.from_payload(payload.scope),
        )
        outcome = await _SERVICE.chunk_check(data)
    except Exception as exc:
        raise _map_domain_error(exc) from exc
    return {
        "request_id": outcome.request_id,
        "candidate_resource": outcome.candidate.to_payload(),
        # complete 只有 true：失败一律报错（403/409/413），不用空数组伪装「查完了没重复」
        "complete": True,
        "evaluated_resources": outcome.evaluated_resources,
        "total_chunks": outcome.total_chunks,
        "duplicate_chunks": outcome.duplicate_chunks,
        "duplicate_ratio": outcome.duplicate_ratio,
        "matches": [match.to_payload() for match in outcome.matches],
    }


# ── #17 查询重复分组 ─────────────────────────────────────────────────


async def query_duplicate_groups(payload: DuplicateGroupsQueryRequest) -> dict[str, Any]:
    """D02：首查全量检测并落 30 分钟只读快照，翻页只读同一份快照。"""
    cursor = _after_cursor(payload.cursor)
    try:
        data = DuplicateGroupsQuery(
            request_id=payload.request_id,
            tenant_id=payload.tenant_id,
            user_id=payload.user_id,
            scope=ScopeData.from_payload(payload.scope),
            kinds=tuple(payload.kinds),
            score_profile=payload.score_profile,
            result_id=payload.result_id,
            cursor=cursor,
            limit=payload.limit,
        )
        page = await _SERVICE.groups(data)
    except Exception as exc:
        raise _map_domain_error(exc) from exc
    briefs = await _briefs([item for group in page.groups for item in group.items])
    return {
        "request_id": page.request_id,
        "result_id": page.result_id,
        "expires_at": page.expires_at.isoformat(),
        "score_profile": page.score_profile,
        "groups": [_group_payload(group, briefs, with_full_text=False) for group in page.groups],
        "next_cursor": page.next_cursor,
        "total_groups": page.total_groups,
        "coverage": page.coverage.to_payload(),
    }


# ── #18 分组详情与对比依据 ───────────────────────────────────────────


async def query_duplicate_group_detail(group_id: str, payload: DuplicateGroupQueryRequest) -> dict[str, Any]:
    """D03：快照分组 + 实时来源补齐全文与证据；成员被修订则 409，不回旧缓存正文。"""
    data = DuplicateGroupQuery(
        request_id=payload.request_id,
        tenant_id=payload.tenant_id,
        user_id=payload.user_id,
        result_id=payload.result_id,
        group_id=group_id,
        scope=ScopeData.from_payload(payload.scope),
    )
    try:
        detail = await _SERVICE.group_detail(data)
    except Exception as exc:
        raise _map_domain_error(exc) from exc
    briefs = await _briefs(detail.group.items)
    return {
        "request_id": detail.request_id,
        "result_id": detail.result_id,
        "group": _group_payload(detail.group, briefs, with_full_text=True),
        "evidence": [evidence.to_payload() for evidence in detail.evidence],
    }


# ── #19 合并预览 ─────────────────────────────────────────────────────


async def generate_merge_preview(payload: MergePreviewRequest) -> dict[str, Any]:
    """D04：按人工指定的主项与处置动作生成草稿态预览，不写正式索引。"""
    data = MergePreviewData(
        request_id=payload.request_id,
        tenant_id=payload.tenant_id,
        user_id=payload.user_id,
        result_id=payload.result_id,
        group_id=payload.group_id,
        scope=ScopeData.from_payload(payload.scope),
        keep_hit_id=payload.keep_hit_id,
        actions=tuple(
            MergeActionData(
                target_hit_id=action.target_hit_id,
                action=action.action,
                merged_answer_html=action.merged_answer_html,
            )
            for action in payload.actions
        ),
        prompt_text=payload.prompt_text,
        model_key=payload.model_key,
    )
    try:
        outcome = await _SERVICE.preview(data)
    except Exception as exc:
        raise _map_domain_error(exc) from exc
    return {
        "request_id": outcome.request_id,
        "preview_id": outcome.preview_id,
        "preview_hash": outcome.preview_hash,
        "title": outcome.title,
        # 非 FAQ 组没有「答案」概念：领域层已按空串返回，这里原样透传，由调用方按 kind 解释
        "answer_html": outcome.answer_html,
        "summary": outcome.summary,
        "source_hit_ids": list(outcome.source_hit_ids),
        "conflicts": [conflict.to_payload() for conflict in outcome.conflicts],
        "warnings": list(outcome.warnings),
    }


__all__ = [
    "check_chunk_duplicates",
    "check_duplicates",
    "generate_merge_preview",
    "query_duplicate_group_detail",
    "query_duplicate_groups",
]
