"""重复治理契约服务（#16–#19）：范围投影 → 领域编排 → 契约投影与错误码映射。

本层只做「契约相关」的三件事，检测算法与存储都在 ``app.modules.kb.dup_result``：

1. **请求投影**：Pydantic 正文 → 领域 dataclass；容量（>1000 项）在这里判 413，
   因为 Pydantic 层只能给 400，而契约明确要求「过大返回 413，不静默截断」；
2. **错误码映射**：领域异常 → 契约码（403/404/409/400），领域层不感知 HTTP；
3. **业务数据补齐**：``items[].subtitle`` 需要 GSI 的受众团队（``modules_gsi``），
   两个领域包不互相 import，只在契约层汇合。

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
    DupScopeDeniedError,
    DupSourceChangedError,
)
from app.modules.kb.dup_result.schema import (
    MAX_SCOPE_ITEMS,
    SCORE_PROFILE,
    DuplicateCheckData,
    DuplicateGroup,
    DuplicateGroupQuery,
    DuplicateGroupsQuery,
    DuplicateHit,
    GovernanceItem,
    MergeActionData,
    MergePreviewData,
    ScopeData,
    SpotChunk,
    SpotDraft,
    SpotResource,
)
from app.modules.kb.dup_result.service import KbDupResultService
from app.modules_gsi.km.doc_publication.schema import SbpKmDocPublicationBrief, publication_subtitle
from app.modules_gsi.km.doc_publication.service import SbpKmDocPublicationService

from ..errors import (
    GOVERNANCE_SCOPE_DENIED,
    INTERNAL_ERROR,
    INVALID_REQUEST,
    NOT_FOUND,
    PAYLOAD_TOO_LARGE,
    RESULT_EXPIRED,
    SOURCE_CHANGED,
    ContractApiError,
)
from .schema import (
    CandidateInput,
    DuplicateCheckRequest,
    DuplicateGroupQueryRequest,
    DuplicateGroupsQueryRequest,
    GovernanceResourceInput,
    GovernanceScope,
    MergePreviewRequest,
    SpotChunkInput,
)

_SERVICE = KbDupResultService()


# ── 请求投影 ─────────────────────────────────────────────────────────


def _resource(resource: GovernanceResourceInput) -> SpotResource:
    return SpotResource(
        resource_type=resource.resource_type,
        resource_id=resource.resource_id,
        record_id=resource.record_id,
        content_hash=resource.content_hash,
        publication_ids=tuple(resource.publication_ids),
        entry_mode=resource.entry_mode,
    )


def _chunk(chunk: SpotChunkInput) -> SpotChunk:
    return SpotChunk(seq=chunk.seq, content=chunk.content, chunk_id=chunk.chunk_id, title=chunk.title)


def _scope(scope: GovernanceScope) -> ScopeData:
    """范围投影；容量超限直接 413，绝不做截断（截断等于少比了内容还报成功）。"""
    if len(scope.resources) > MAX_SCOPE_ITEMS or len(scope.drafts) > MAX_SCOPE_ITEMS:
        raise ContractApiError(
            PAYLOAD_TOO_LARGE,
            f"scope.resources / scope.drafts 最多 {MAX_SCOPE_ITEMS} 项，本次请求超过上限，请缩小范围后重试。",
        )
    return ScopeData(
        scope_id=scope.scope_id,
        resources=tuple(_resource(resource) for resource in scope.resources),
        drafts=tuple(
            SpotDraft(
                resource=_resource(draft.resource),
                title=draft.title,
                content=draft.content,
                summary=draft.summary,
                chunks=tuple(_chunk(chunk) for chunk in draft.chunks),
            )
            for draft in scope.drafts
        ),
    )


def _candidate(candidate: CandidateInput) -> GovernanceItem:
    """候选内容只参与本次比较：``is_draft=True`` 保证不落任何正式表。"""
    return GovernanceItem(
        resource=_resource(candidate.resource),
        title=candidate.title,
        content=candidate.content,
        summary=candidate.summary,
        chunks=tuple(_chunk(chunk) for chunk in candidate.chunks),
        is_draft=True,
    )


def _score_profile(value: str) -> str:
    """策略版本必须与实现一致：静默按 v1 计算再把 v2 回给调用方，等于让相似度不可解释。"""
    if value != SCORE_PROFILE:
        raise ContractApiError(
            INVALID_REQUEST,
            f"score_profile 只支持 {SCORE_PROFILE}，收到 {value}：不同策略的相似度不可比较。",
        )
    return SCORE_PROFILE


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
    if isinstance(exc, DupScopeDeniedError):
        return ContractApiError(GOVERNANCE_SCOPE_DENIED, str(exc))
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
    data = DuplicateCheckData(
        request_id=payload.request_id,
        tenant_id=payload.tenant_id,
        user_id=payload.user_id,
        candidate=_candidate(payload.candidate),
        scope=_scope(payload.scope),
        score_profile=_score_profile(payload.score_profile),
        kinds=tuple(payload.kinds),
    )
    try:
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


# ── #17 查询重复分组 ─────────────────────────────────────────────────


async def query_duplicate_groups(payload: DuplicateGroupsQueryRequest) -> dict[str, Any]:
    """D02：首查全量检测并落 30 分钟只读快照，翻页只读同一份快照。"""
    data = DuplicateGroupsQuery(
        request_id=payload.request_id,
        tenant_id=payload.tenant_id,
        user_id=payload.user_id,
        scope=_scope(payload.scope),
        kinds=tuple(payload.kinds),
        score_profile=_score_profile(payload.score_profile),
        result_id=payload.result_id,
        cursor=_after_cursor(payload.cursor),
        limit=payload.limit,
    )
    try:
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
        scope=_scope(payload.scope),
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
        scope=_scope(payload.scope),
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
    "check_duplicates",
    "generate_merge_preview",
    "query_duplicate_group_detail",
    "query_duplicate_groups",
]
