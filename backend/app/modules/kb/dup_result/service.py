"""重复检测与治理编排（kb 基座 service 层）。

职责边界（与其它层的分工）：

- ``source`` 负责「范围 → 可比较内容」，并保证内容与 scope 声明一致（不符即 409）；
- ``detector`` 是纯函数，只做分组与相似度；
- **本层**只做编排：取内容 → 检测 → 落只读快照 → 物化治理行 → 分页/详情/预览；
- 契约字段校验与错误码映射在 ``modules_api/kb/duplicate``，本层只抛领域异常。

两条贯穿实现的原则：

1. **失败不降级**：读不到内容、正文变了、结果过期一律显式报错。返回「未发现重复」
   会让调用方以为可以保存，这正是 §九 禁止的失败表现。
2. **三步看同一份结果**：D02 首查把分组全量写进 ``sbp_ai_kb_dup_result``（保留 30 分钟），
   D02 翻页 / D03 详情 / D04 预览都只读这份快照，再拿实时来源**重新校验**，
   既不重算（翻页会前后矛盾），也不盲信快照（内容可能已被修订）。
"""

import html
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_factory
from app.core.logger import logger
from app.utils.common_util import canonical_json_sha256, uuid4_str

from ..dup_group.crud import KbDupGroupCRUD
from ..dup_group.service import KbDupGroupService, KbDupGroupSyncData
from .crud import KbDupResultCRUD
from .detector import check_candidate, detect_groups, group_evidence, normalize_text
from .errors import (
    DupInvalidRequestError,
    DupResultExpiredError,
    DupResultNotFoundError,
    DupSourceChangedError,
)
from .model import KbDupResultModel
from .schema import (
    DEFAULT_GROUP_PAGE,
    FULL_TEXT_LIMIT,
    HIT_CHUNK,
    KIND_CHUNK,
    KIND_FAQ_AI,
    KIND_FAQ_MANUAL,
    MAX_GROUP_PAGE,
    RESULT_TTL,
    DuplicateCheckData,
    DuplicateCheckOutcome,
    DuplicateCoverage,
    DuplicateGroup,
    DuplicateGroupDetail,
    DuplicateGroupQuery,
    DuplicateGroupsPage,
    DuplicateGroupsQuery,
    DuplicateHit,
    GovernanceItem,
    MergeConflict,
    MergePreviewData,
    MergePreviewOutcome,
    ScopeData,
    content_digest,
    scope_digest,
    scope_payload,
)
from .source import KbDupSource

# 调用方给的合并正文可能自带标签：先按块级标签断行、剥掉其余标签，再统一包 <p>，
# 保证输出里的标签只有本层生成的那几种（不透传任意 HTML）
_BLOCK_TAG_RE = re.compile(r"</?(?:p|div|br|li|ul|ol|h[1-6]|tr|td|section|article)\b[^>]*>", re.IGNORECASE)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"[ \t\r\f\v]+")
# 「字段：值」形式的可比对事实（FAQ 答案里常见），用于检出无法自动合并的矛盾
_FACT_RE = re.compile(r"^\s*([^\s：:]{2,16})\s*[：:]\s*(.+?)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class _MergePlan:
    """合并预览（草稿态，只暂存 ``dup_group.merged_preview``，不落正式库）。"""

    group_id: str
    keep_hit_id: str
    title: str
    answer_html: str
    summary: str
    source_hit_ids: tuple[str, ...]
    conflicts: tuple[MergeConflict, ...]
    warnings: tuple[str, ...]
    preview_id: str
    preview_hash: str
    actions: tuple[dict[str, Any], ...]
    prompt_text: str
    model_key: str
    created_time: datetime

    def to_payload(self) -> dict[str, Any]:
        return {
            "preview_id": self.preview_id,
            "preview_hash": self.preview_hash,
            "group_id": self.group_id,
            "keep_hit_id": self.keep_hit_id,
            "actions": list(self.actions),
            "title": self.title,
            "answer_html": self.answer_html,
            "summary": self.summary,
            "source_hit_ids": list(self.source_hit_ids),
            "conflicts": [conflict.to_payload() for conflict in self.conflicts],
            "warnings": list(self.warnings),
            "prompt_text": self.prompt_text,
            "model_key": self.model_key,
            "created_time": self.created_time.isoformat(),
        }


class KbDupResultService:
    """重复治理服务：检测、查询、详情与合并预览。

    刻意不在 ``__init__`` 里持有会话：契约请求是「一次调用一个事务」，
    由每个方法自建 ``async_session_factory()``，避免长事务挂住连接。
    """

    # ── D01 保存前查重 ────────────────────────────────────────────────

    async def check(self, data: DuplicateCheckData) -> DuplicateCheckOutcome:
        """候选内容 vs 授权范围：只报命中，不落库（保存动作由 GSI 自己决定）。"""
        async with async_session_factory() as session:
            items = await KbDupSource(session).load(scope=data.scope, tenant_id=data.tenant_id)
        matches, similarity = check_candidate(data.candidate, list(items.values()), data.kinds)
        logger.info(
            "保存前查重完成：request_id={} tenant={} candidate={} matches={}",
            data.request_id,
            data.tenant_id,
            data.candidate.identity,
            len(matches),
        )
        return DuplicateCheckOutcome(
            request_id=data.request_id,
            is_duplicate=bool(matches),
            similarity=similarity,
            score_profile=data.score_profile,
            matches=matches,
        )

    # ── D02 查询重复分组 ─────────────────────────────────────────────

    async def groups(self, data: DuplicateGroupsQuery) -> DuplicateGroupsPage:
        """首查做全量检测并落快照；带 ``result_id`` 则只在同一份快照上翻页。"""
        async with async_session_factory() as session, session.begin():
            if data.result_id:
                row = await self._load_result(
                    session,
                    result_id=data.result_id,
                    tenant_id=data.tenant_id,
                    user_id=data.user_id,
                    scope=data.scope,
                    kinds=data.kinds,
                    score_profile=data.score_profile,
                )
                groups = [DuplicateGroup.from_payload(payload) for payload in row.groups or []]
                coverage = self._coverage_of(row)
            else:
                row, groups, coverage = await self._detect_and_store(session, data)
            page, next_cursor = self._slice(groups, cursor=data.cursor, limit=data.limit)
            return DuplicateGroupsPage(
                request_id=data.request_id,
                result_id=row.uuid,
                expires_at=row.expires_at,
                score_profile=row.score_profile,
                groups=tuple(page),
                next_cursor=next_cursor,
                total_groups=int(row.total_groups or 0),
                coverage=coverage,
            )

    # ── D03 分组详情 ─────────────────────────────────────────────────

    async def group_detail(self, data: DuplicateGroupQuery) -> DuplicateGroupDetail:
        """读快照里的分组，并用实时来源补齐全文与证据（同时校验成员未被修订）。"""
        async with async_session_factory() as session:
            row = await self._load_result(
                session,
                result_id=data.result_id,
                tenant_id=data.tenant_id,
                user_id=data.user_id,
                scope=data.scope,
            )
            group = DuplicateGroup.from_payload(self._find_group(row, data.group_id))
            items = await KbDupSource(session).load(scope=data.scope, tenant_id=data.tenant_id)
        self._assert_members_unchanged(group, items)
        enriched = self._enrich_full_text(group, items)
        return DuplicateGroupDetail(
            request_id=data.request_id,
            result_id=data.result_id,
            group=enriched,
            evidence=group_evidence(enriched),
        )

    # ── D04 合并预览 ─────────────────────────────────────────────────

    async def preview(self, data: MergePreviewData) -> MergePreviewOutcome:
        """按人工指定的主项与处置动作生成合并预览：只产出草稿态结果，不动正式库。"""
        async with async_session_factory() as session, session.begin():
            row = await self._load_result(
                session,
                result_id=data.result_id,
                tenant_id=data.tenant_id,
                user_id=data.user_id,
                scope=data.scope,
            )
            group = DuplicateGroup.from_payload(self._find_group(row, data.group_id))
            items = await KbDupSource(session).load(scope=data.scope, tenant_id=data.tenant_id)
            self._assert_members_unchanged(group, items)
            plan = self._plan(
                group=self._enrich_full_text(group, items),
                data=data,
                has_draft=any(items[hit.resource.identity].is_draft for hit in group.items),
            )
            await self._save_preview(session, tenant_id=data.tenant_id, group_key=group.group_id, plan=plan)

        logger.info(
            "合并预览生成：request_id={} result_id={} group_id={} keep={} conflicts={}",
            data.request_id,
            data.result_id,
            data.group_id,
            data.keep_hit_id,
            len(plan.conflicts),
        )
        return MergePreviewOutcome(
            request_id=data.request_id,
            preview_id=plan.preview_id,
            preview_hash=plan.preview_hash,
            title=plan.title,
            answer_html=plan.answer_html,
            summary=plan.summary,
            source_hit_ids=plan.source_hit_ids,
            conflicts=plan.conflicts,
            warnings=plan.warnings,
        )

    # ── 检测与落库 ───────────────────────────────────────────────────

    async def _detect_and_store(
        self, session: AsyncSession, data: DuplicateGroupsQuery
    ) -> tuple[KbDupResultModel, list[DuplicateGroup], DuplicateCoverage]:
        """范围内全量检测 → 写只读快照 → 物化治理行。

        三步在同一个事务里：快照与治理行必须同时出现，否则会出现「能翻页但治理台账缺项」，
        或反过来「台账有重复却翻不到分组」。
        """
        items = await KbDupSource(session).load(scope=data.scope, tenant_id=data.tenant_id)
        groups = detect_groups(list(items.values()), data.kinds)
        coverage = DuplicateCoverage(
            requested_resources=data.scope.requested_resources,
            evaluated_resources=len(items),
        )
        row = await self._store(session, data=data, groups=groups, coverage=coverage)
        await self._materialize(session, data=data, groups=groups, result_id=row.uuid)
        logger.info(
            "重复检测完成：request_id={} result_id={} tenant={} resources={} groups={}",
            data.request_id,
            row.uuid,
            data.tenant_id,
            coverage.evaluated_resources,
            len(groups),
        )
        return row, groups, coverage

    async def _store(
        self,
        session: AsyncSession,
        *,
        data: DuplicateGroupsQuery,
        groups: list[DuplicateGroup],
        coverage: DuplicateCoverage,
    ) -> KbDupResultModel:
        """写 30 分钟只读快照：自包含分组，翻页/详情不再重算。"""
        expires_at = datetime.now(UTC) + RESULT_TTL
        return await KbDupResultCRUD(session).create(
            data={
                # uuid 即契约 result_id，前面加前缀便于日志与排障时一眼认出来源
                "uuid": f"dup-result-{uuid4_str()}",
                "tenant_id": data.tenant_id,
                "user_id": data.user_id,
                "score_profile": data.score_profile,
                "kinds": list(data.kinds),
                "scope": scope_payload(data.scope),
                "scope_digest": scope_digest(data.scope, data.kinds, data.score_profile),
                "content_digest": content_digest(data.scope),
                "groups": [group.to_payload() for group in groups],
                "coverage": coverage.to_payload(),
                "total_groups": len(groups),
                "expires_at": expires_at,
            }
        )

    async def _materialize(
        self,
        session: AsyncSession,
        *,
        data: DuplicateGroupsQuery,
        groups: list[DuplicateGroup],
        result_id: str,
    ) -> None:
        """把分组按稳定 ``group_key`` 物化成治理行（人工结论由 dup_group 服务保持）。"""
        if not groups:
            return
        scope_snapshot = scope_payload(data.scope)
        group_service = KbDupGroupService(session)
        for group in groups:
            await group_service.sync_group(
                KbDupGroupSyncData(
                    tenant_id=data.tenant_id,
                    group_key=group.group_id,
                    kind=group.kind,
                    title=group.title[:255],
                    score_method=group.score_method,
                    reason=group.reason[:500],
                    suggested_keep_id=group.suggested_keep_id,
                    result_id=result_id,
                    resource_type=group.items[0].resource.resource_type,
                    scope=scope_snapshot,
                    members=[item.to_payload(with_full_text=True) for item in group.items],
                    similarity=group.similarity,
                )
            )

    # ── 快照读取与校验 ───────────────────────────────────────────────

    async def _load_result(
        self,
        session: AsyncSession,
        *,
        result_id: str,
        tenant_id: str,
        user_id: str,
        scope: ScopeData,
        kinds: tuple[str, ...] | None = None,
        score_profile: str | None = None,
    ) -> KbDupResultModel:
        """读快照并校验「还是不是同一次治理」；任何不符都必须报错，不能退化成重算。"""
        row = await KbDupResultCRUD(session).get_by_result_id(result_id)
        # 租户/用户不符按「不存在」处理：不泄露他人检测结果的存在
        if row is None or row.tenant_id != tenant_id or row.user_id != user_id:
            raise DupResultNotFoundError(f"检测结果 {result_id} 不存在或不属于当前租户。")
        if self._as_utc(row.expires_at) <= datetime.now(UTC):
            raise DupResultExpiredError(f"检测结果 {result_id} 已超过 {RESULT_TTL} 的保留期，请重新发起检测。")

        stored_kinds = tuple(row.kinds or ())
        if kinds is not None and sorted(kinds) != sorted(stored_kinds):
            raise DupSourceChangedError("本次请求的治理类型与结果快照不一致，请重新发起检测。")
        if score_profile is not None and score_profile != row.score_profile:
            raise DupSourceChangedError("本次请求的检测策略与结果快照不一致，请重新发起检测。")
        if scope_digest(scope, stored_kinds, row.score_profile) != row.scope_digest:
            raise DupSourceChangedError("授权范围或内容摘要已变化，请在当前范围重新发起检测。")
        return row

    @staticmethod
    def _find_group(row: KbDupResultModel, group_id: str) -> dict[str, Any]:
        for payload in row.groups or []:
            if str(payload.get("group_id")) == group_id:
                return payload
        raise DupResultNotFoundError(f"分组 {group_id} 不在检测结果 {row.uuid} 中。")

    @staticmethod
    def _coverage_of(row: KbDupResultModel) -> DuplicateCoverage:
        payload = row.coverage or {}
        return DuplicateCoverage(
            requested_resources=int(payload.get("requested_resources") or 0),
            evaluated_resources=int(payload.get("evaluated_resources") or 0),
            complete=bool(payload.get("complete", True)),
        )

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        """SQLite（测试）会丢时区，PostgreSQL 不会：统一按 UTC 比较，避免 naive/aware 相减报错。"""
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)

    @staticmethod
    def _slice(
        groups: list[DuplicateGroup], *, cursor: int, limit: int
    ) -> tuple[list[DuplicateGroup], str | None]:
        start = max(int(cursor or 0), 0)
        size = min(max(int(limit or DEFAULT_GROUP_PAGE), 1), MAX_GROUP_PAGE)
        page = groups[start : start + size]
        next_cursor = str(start + len(page)) if start + len(page) < len(groups) else None
        return page, next_cursor

    @staticmethod
    def _assert_members_unchanged(group: DuplicateGroup, items: dict[str, GovernanceItem]) -> None:
        """快照里的成员必须与实时来源逐一对得上：内容变了就不能再拿旧检测结果治理。"""
        for hit in group.items:
            item = items.get(hit.resource.identity)
            if item is None:
                raise DupSourceChangedError(f"资源 {hit.resource.identity} 已不在本次授权范围内，请重新发起检测。")
            if (
                item.resource.record_id != hit.resource.record_id
                or item.resource.content_hash != hit.resource.content_hash
            ):
                raise DupSourceChangedError(
                    f"资源 {hit.resource.identity} 的生效快照或内容摘要已变化，请重新发起检测。"
                )

    @staticmethod
    def _text_of(item: GovernanceItem, hit: DuplicateHit) -> str:
        """按命中类型取全文：切片取对应切片，整篇取标题 + 正文。"""
        if hit.hit_kind != HIT_CHUNK:
            return f"{item.title}：{item.content}"[:FULL_TEXT_LIMIT]
        for chunk in item.chunks:
            if hit.chunk_id and chunk.chunk_id == hit.chunk_id:
                return chunk.content[:FULL_TEXT_LIMIT]
            if not hit.chunk_id and hit.source_seq is not None and chunk.seq == hit.source_seq:
                return chunk.content[:FULL_TEXT_LIMIT]
        raise DupSourceChangedError(f"资源 {hit.resource.identity} 的切片 {hit.chunk_id or hit.source_seq} 已不存在。")

    def _enrich_full_text(
        self, group: DuplicateGroup, items: dict[str, GovernanceItem]
    ) -> DuplicateGroup:
        """快照为了省空间不存全文，详情/预览时按实时来源补齐（引用与全文取自同一份文本）。"""
        hits = tuple(
            replace(hit, full_text=self._text_of(items[hit.resource.identity], hit)) for hit in group.items
        )
        return replace(group, items=hits)

    # ── 合并预览 ─────────────────────────────────────────────────────

    def _plan(self, *, group: DuplicateGroup, data: MergePreviewData, has_draft: bool) -> _MergePlan:
        """校验处置动作并生成合并预览（纯计算，不写库）。"""
        hits = {hit.hit_id: hit for hit in group.items}
        if data.keep_hit_id not in hits:
            raise DupInvalidRequestError(f"主项 {data.keep_hit_id} 不在分组 {group.group_id} 内。")

        actions: dict[str, str] = {}
        merged_answers: dict[str, str] = {}
        for action in data.actions:
            if action.target_hit_id not in hits:
                raise DupInvalidRequestError(f"处置目标 {action.target_hit_id} 不在分组 {group.group_id} 内。")
            if action.target_hit_id in actions:
                raise DupInvalidRequestError(f"处置目标 {action.target_hit_id} 重复出现，每个成员只能有一个动作。")
            actions[action.target_hit_id] = action.action
            if action.merged_answer_html:
                merged_answers[action.target_hit_id] = action.merged_answer_html
        missing = [hit_id for hit_id in hits if hit_id not in actions]
        if missing:
            raise DupInvalidRequestError(f"分组内成员 {', '.join(sorted(missing))} 未指定处置动作。")
        if actions.get(data.keep_hit_id) != "keep":
            raise DupInvalidRequestError("被指定为主项的成员，其处置动作必须是 keep。")

        keep = hits[data.keep_hit_id]
        merged_hit_ids = tuple(sorted(hit_id for hit_id, action in actions.items() if action == "merge_answer"))
        for hit_id in merged_hit_ids:
            if group.kind == KIND_CHUNK or hits[hit_id].hit_kind == HIT_CHUNK:
                raise DupInvalidRequestError("切片重复不能合并答案，只能保留或丢弃。")
            if group.kind not in (KIND_FAQ_AI, KIND_FAQ_MANUAL):
                raise DupInvalidRequestError("只有问答类分组合并才允许 merge_answer。")

        source_hit_ids = (keep.hit_id, *merged_hit_ids)
        paragraphs = self._paragraphs(source_hit_ids, hits, merged_answers)
        answer_html = "".join(f"<p>{paragraph}</p>" for paragraph in paragraphs)
        conflicts = self._conflicts(source_hit_ids, hits)
        warnings = self._warnings(
            group=group,
            hits=hits,
            actions=actions,
            keep_hit_id=data.keep_hit_id,
            has_draft=has_draft,
        )
        created_time = datetime.now(UTC)
        plan = _MergePlan(
            group_id=group.group_id,
            keep_hit_id=keep.hit_id,
            title=keep.title,
            answer_html=answer_html,
            summary=self._summary_text(group.kind, keep.title, len(source_hit_ids)),
            source_hit_ids=source_hit_ids,
            conflicts=conflicts,
            warnings=warnings,
            preview_id=f"dup-preview-{uuid4_str()[:12]}",
            preview_hash="",
            actions=tuple(
                {"target_hit_id": hit_id, "action": action} for hit_id, action in sorted(actions.items())
            ),
            prompt_text=data.prompt_text,
            model_key=data.model_key,
            created_time=created_time,
        )
        # 预览摘要只覆盖「内容决策」：同一组输入重复调用应当得到同一个 hash（便于去重与审计），
        # 因此排除随机 preview_id 与生成时间
        payload = plan.to_payload()
        payload.pop("preview_id")
        payload.pop("created_time")
        return replace(plan, preview_hash="sha256:" + canonical_json_sha256(payload))

    @staticmethod
    def _paragraphs(
        source_hit_ids: tuple[str, ...],
        hits: dict[str, DuplicateHit],
        merged_answers: dict[str, str],
    ) -> tuple[str, ...]:
        """按序拼段落：同一结论只保留一份（归一化后重复的段落直接丢弃）。"""
        paragraphs: list[str] = []
        seen: set[str] = set()
        for hit_id in source_hit_ids:
            raw = merged_answers.get(hit_id) or ""
            candidates = [raw] if raw else [hits[hit_id].full_text or hits[hit_id].snippet]
            for candidate in candidates:
                for line in _BLOCK_TAG_RE.sub("\n", candidate).split("\n"):
                    text = _SPACE_RE.sub(" ", _HTML_TAG_RE.sub("", line)).strip()
                    key = normalize_text(text)
                    if not key or key in seen:
                        continue
                    seen.add(key)
                    paragraphs.append(html.escape(text))
        return tuple(paragraphs)

    @staticmethod
    def _conflicts(source_hit_ids: tuple[str, ...], hits: dict[str, DuplicateHit]) -> tuple[MergeConflict, ...]:
        """「字段：值」的事实矛盾：同字段出现互不相交的取值时必须人工处理。"""
        facts: dict[str, dict[str, list[str]]] = {}
        for hit_id in source_hit_ids:
            text = hits[hit_id].full_text or hits[hit_id].snippet
            for field, value in _FACT_RE.findall(text):
                key = normalize_text(field)
                bucket = facts.setdefault(key, {})
                bucket.setdefault(normalize_text(value), []).append(hit_id)
        conflicts: list[MergeConflict] = []
        for key, values in facts.items():
            if len(values) < 2:
                continue
            # 取值互不包含才算矛盾：一方是另一方的前缀（如「3 天」与「3 天内」）属于表述差异
            normalized = list(values)
            if all(
                left in right or right in left
                for index, left in enumerate(normalized)
                for right in normalized[index + 1 :]
            ):
                continue
            hit_ids = tuple(sorted({hit_id for ids in values.values() for hit_id in ids}))
            conflicts.append(
                MergeConflict(
                    field=key,
                    description=f"字段「{key}」在成员中的取值不一致：{'/'.join(sorted(values))}。",
                    hit_ids=hit_ids,
                )
            )
        return tuple(conflicts)

    @staticmethod
    def _warnings(
        *,
        group: DuplicateGroup,
        hits: dict[str, DuplicateHit],
        actions: dict[str, str],
        keep_hit_id: str,
        has_draft: bool,
    ) -> tuple[str, ...]:
        """必须让人看见的风险：草稿参与、与推荐主项不同、丢弃成员。"""
        warnings: list[str] = []
        if has_draft:
            warnings.append("分组包含尚未入库的草稿内容，合并前请确认草稿已保存。")
        if keep_hit_id != group.suggested_keep_id:
            warnings.append(
                f"人工指定的主项与系统推荐不一致（推荐 {group.suggested_keep_id}），"
                "请确认保留项的内容完整。"
            )
        dropped = sorted(hit_id for hit_id, action in actions.items() if action in ("drop", "discard"))
        if dropped:
            warnings.append(f"处置动作将丢弃 {len(dropped)} 个成员：{', '.join(dropped)}。")
        if len(hits) > 2:
            warnings.append(f"该分组包含 {len(hits)} 个成员，建议逐对比对后再提交审批。")
        return tuple(warnings)

    @staticmethod
    def _summary_text(kind: str, title: str, source_count: int) -> str:
        if kind == KIND_CHUNK:
            return f"合并 {source_count} 个重复切片，保留主项切片，其余切片不再参与检索。"
        if kind in (KIND_FAQ_AI, KIND_FAQ_MANUAL):
            return f"合并 {source_count} 条重复问答，保留《{title}》并统一答案。"
        return f"合并 {source_count} 条重复知识，保留《{title}》并补充区分信息。"

    async def _save_preview(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        group_key: str,
        plan: _MergePlan,
    ) -> None:
        """预览暂存在治理行上：分组已被人工处理或尚未物化时静默跳过（预览本身仍可用）。"""
        row = await KbDupGroupCRUD(session).get_by_group_key(tenant_id=tenant_id, group_key=group_key)
        if row is None or row.status != "active":
            return
        await KbDupGroupCRUD(session).update(id=row.id, data={"merged_preview": plan.to_payload()})

__all__ = ["KbDupResultService"]
