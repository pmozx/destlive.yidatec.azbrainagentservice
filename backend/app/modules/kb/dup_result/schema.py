"""重复治理领域模型（kb 基座 schema 层）：策略常量 + 领域 dataclass + 摘要计算。

本文件不依赖 Pydantic：``detector``（纯函数）/ ``source``（范围取内容）/ ``service``（编排）
与 ``modules_api`` 契约层各自投影，字段口径只在这里定义一次。

两个摘要是「结果快照能不能复用」的唯一依据（§九：结果绑定租户、用户、范围摘要与内容摘要）：
``scope_digest`` 覆盖「范围 + 内容 + 策略」，不含 ``scope_id`` / 时间戳 —— GSI 每次翻页都会
重新签发 scope，只有排除这三个易变字段，同一次治理才能沿用同一个 result_id。
"""

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from app.utils.common_util import canonical_json_sha256

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# §一  策略常量（score_profile 的算法与阈值版本，改这里等于换策略版本）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# 五类治理分组（契约 D02/D03 的 kinds 枚举，顺序即展示顺序）
KIND_DOC_EXACT = "doc_exact"
KIND_DOC_SIMILAR = "doc_similar"
KIND_CHUNK = "chunk"
KIND_FAQ_AI = "faq_ai"
KIND_FAQ_MANUAL = "faq_manual"
DUP_KINDS: tuple[str, ...] = (KIND_DOC_EXACT, KIND_DOC_SIMILAR, KIND_CHUNK, KIND_FAQ_AI, KIND_FAQ_MANUAL)

RESOURCE_DOCUMENT = "document"
RESOURCE_FAQ = "faq"
RESOURCE_TYPES: tuple[str, ...] = (RESOURCE_DOCUMENT, RESOURCE_FAQ)

# hit_kind：doc=整篇，faq=问答，chunk=单切片
HIT_DOC = "doc"
HIT_FAQ = "faq"
HIT_CHUNK = "chunk"

# 分组类型 → 命中类型（契约里两者是不同字段，不能混用）
KIND_HIT: dict[str, str] = {
    KIND_DOC_EXACT: HIT_DOC,
    KIND_DOC_SIMILAR: HIT_DOC,
    KIND_CHUNK: HIT_CHUNK,
    KIND_FAQ_AI: HIT_FAQ,
    KIND_FAQ_MANUAL: HIT_FAQ,
}

SCORE_PROFILE = "gsi-duplicate-v1"
# score_method：检测方法与版本，契约要求「不能直接套搜索相关度」
METHOD_TITLE_EXACT = "title_exact_v1"
METHOD_TITLE_SIMILARITY = "title_similarity_v1"
METHOD_CONTENT_SIMILARITY = "content_similarity_v1"
METHOD_CHUNK_EXACT = "chunk_exact_v1"

# 判定阈值：2-gram Dice/包含度混合口径下，0.65 能同时覆盖「标题近似」与「正文高度重合」。
# 阈值属于策略的一部分，改动必须同步 score_profile，否则调用方拿到的相似度不可解释。
SIMILARITY_THRESHOLD = 0.65

# 结果快照保留时长与范围容量（§九：保留 30 分钟、最多 1000 个资源、超限 413）
RESULT_TTL = timedelta(minutes=30)
MAX_SCOPE_ITEMS = 1000
DEFAULT_GROUP_PAGE = 20
MAX_GROUP_PAGE = 100
MAX_MATCHES = 50

# 比较与展示的长度上限：只影响「相似度算多长」「快照截多长」，不参与落库判定
COMPARE_TEXT_LIMIT = 2000
FULL_TEXT_LIMIT = 4000
SNIPPET_LIMIT = 120


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# §二  领域结构（请求投影 / 检测项 / 分组 / 证据）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@dataclass(frozen=True)
class SpotChunk:
    """治理范围内的一个切片：``chunk_id`` 为空表示尚未入库的草稿切片。"""

    seq: int
    content: str
    chunk_id: str | None = None
    title: str | None = None


@dataclass(frozen=True)
class SpotResource:
    """治理对象的不可变内容身份：``resource_type + resource_id`` 定位业务知识，
    ``record_id + content_hash`` 定位本次比较版本。"""

    resource_type: str
    resource_id: str
    record_id: str
    content_hash: str
    publication_ids: tuple[str, ...] = ()
    entry_mode: str = "manual"

    @property
    def identity(self) -> str:
        """业务知识身份：同一内容多团队发布只参与比较一次，所以不含 publication_ids。"""
        return f"{self.resource_type}:{self.resource_id}"


@dataclass(frozen=True)
class SpotDraft:
    """scope.drafts 里的一条草稿内容：AI 尚无正式内容时由 GSI 随请求临时提供。"""

    resource: SpotResource
    title: str
    content: str
    summary: str | None = None
    chunks: tuple[SpotChunk, ...] = ()


@dataclass(frozen=True)
class ScopeData:
    """一次治理请求的授权范围。"""

    scope_id: str
    resources: tuple[SpotResource, ...] = ()
    drafts: tuple[SpotDraft, ...] = ()

    @property
    def requested_resources(self) -> int:
        return len(self.resources)


@dataclass(frozen=True)
class GovernanceItem:
    """参与比较的一个对象（正式内容或草稿），由 ``source`` 从范围还原。"""

    resource: SpotResource
    title: str
    content: str
    summary: str | None = None
    chunks: tuple[SpotChunk, ...] = ()
    is_draft: bool = False
    created_time: datetime | None = None

    @property
    def identity(self) -> str:
        return self.resource.identity


@dataclass(frozen=True)
class DuplicateHit:
    """检测项（契约 matches[] / groups[].items[] 同构）。"""

    hit_id: str
    hit_kind: str
    resource: SpotResource
    title: str
    snippet: str
    similarity: float
    reason: str
    chunk_id: str | None = None
    source_seq: int | None = None
    full_text: str | None = None

    def to_payload(self, *, with_full_text: bool = False) -> dict[str, Any]:
        """契约投影；``full_text`` 只在详情/预览里出现（列表省略，避免分组响应膨胀）。"""
        payload: dict[str, Any] = {
            "hit_id": self.hit_id,
            "hit_kind": self.hit_kind,
            "resource": _resource_payload(self.resource),
            "title": self.title,
            "snippet": self.snippet,
            "similarity": self.similarity,
            "reason": self.reason,
        }
        if self.chunk_id is not None:
            payload["chunk_id"] = self.chunk_id
        if self.source_seq is not None:
            payload["source_seq"] = self.source_seq
        if with_full_text:
            payload["full_text"] = self.full_text or ""
        return payload


@dataclass(frozen=True)
class DuplicateGroup:
    """一个重复分组；``group_id`` 是「类型 + 排序后的成员身份」的稳定摘要。"""

    group_id: str
    kind: str
    title: str
    similarity: float
    score_method: str
    reason: str
    items: tuple[DuplicateHit, ...]
    suggested_keep_id: str

    def to_payload(self, *, with_full_text: bool = False) -> dict[str, Any]:
        return {
            "group_id": self.group_id,
            "kind": self.kind,
            "title": self.title,
            "similarity": self.similarity,
            "score_method": self.score_method,
            "reason": self.reason,
            "items": [item.to_payload(with_full_text=with_full_text) for item in self.items],
            "suggested_keep_id": self.suggested_keep_id,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "DuplicateGroup":
        """快照回读：结果快照里存的是契约投影，翻页/详情必须还原成同一结构再投影一次。"""
        return cls(
            group_id=str(payload["group_id"]),
            kind=str(payload["kind"]),
            title=str(payload.get("title") or ""),
            similarity=float(payload.get("similarity") or 0.0),
            score_method=str(payload.get("score_method") or METHOD_TITLE_SIMILARITY),
            reason=str(payload.get("reason") or ""),
            items=tuple(_hit_from_payload(item) for item in payload.get("items") or []),
            suggested_keep_id=str(payload.get("suggested_keep_id") or ""),
        )


@dataclass(frozen=True)
class GroupEvidence:
    """成对原文依据（契约 evidence[]）：引用必须能在 ``full_text`` 里找到。"""

    left_hit_id: str
    right_hit_id: str
    basis: str
    left_quote: str
    right_quote: str
    explanation: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "left_hit_id": self.left_hit_id,
            "right_hit_id": self.right_hit_id,
            "basis": self.basis,
            "left_quote": self.left_quote,
            "right_quote": self.right_quote,
            "explanation": self.explanation,
        }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# §三  服务层入参/出参
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@dataclass(frozen=True)
class DuplicateCheckData:
    """D01 保存前查重。"""

    request_id: str
    tenant_id: str
    user_id: str
    candidate: GovernanceItem
    scope: ScopeData
    score_profile: str = SCORE_PROFILE
    kinds: tuple[str, ...] = DUP_KINDS


@dataclass(frozen=True)
class DuplicateGroupsQuery:
    """D02 查询重复分组：首查带 scope，翻页带 result_id。"""

    request_id: str
    tenant_id: str
    user_id: str
    scope: ScopeData
    kinds: tuple[str, ...] = DUP_KINDS
    score_profile: str = SCORE_PROFILE
    result_id: str | None = None
    cursor: int = 0
    limit: int = DEFAULT_GROUP_PAGE


@dataclass(frozen=True)
class DuplicateGroupQuery:
    """D03 分组详情与对比依据。"""

    request_id: str
    tenant_id: str
    user_id: str
    result_id: str
    group_id: str
    scope: ScopeData


@dataclass(frozen=True)
class MergeActionData:
    """D04 单个成员的处置动作。"""

    target_hit_id: str
    action: str
    merged_answer_html: str | None = None


MERGE_ACTIONS: tuple[str, ...] = ("keep", "drop", "discard", "merge_answer")


@dataclass(frozen=True)
class MergePreviewData:
    """D04 生成合并预览（不落正式库）。"""

    request_id: str
    tenant_id: str
    user_id: str
    result_id: str
    group_id: str
    scope: ScopeData
    keep_hit_id: str
    actions: tuple[MergeActionData, ...] = ()
    prompt_text: str = ""
    model_key: str = "default"


@dataclass(frozen=True)
class DuplicateCoverage:
    """覆盖度：只代表「指定范围」完整，不能声称全库无重复。"""

    requested_resources: int
    evaluated_resources: int
    complete: bool = True

    def to_payload(self) -> dict[str, Any]:
        return {
            "requested_resources": self.requested_resources,
            "evaluated_resources": self.evaluated_resources,
            "complete": self.complete,
        }


@dataclass(frozen=True)
class DuplicateCheckOutcome:
    """D01 结果。"""

    request_id: str
    is_duplicate: bool
    similarity: float
    score_profile: str
    matches: tuple[DuplicateHit, ...]


@dataclass(frozen=True)
class DuplicateGroupsPage:
    """D02 结果（一页分组 + 该结果的全部分组数）。"""

    request_id: str
    result_id: str
    expires_at: datetime
    score_profile: str
    groups: tuple[DuplicateGroup, ...]
    next_cursor: str | None
    total_groups: int
    coverage: DuplicateCoverage


@dataclass(frozen=True)
class DuplicateGroupDetail:
    """D03 结果。"""

    request_id: str
    result_id: str
    group: DuplicateGroup
    evidence: tuple[GroupEvidence, ...]


@dataclass(frozen=True)
class MergeConflict:
    """未能自动解决的事实矛盾，必须人工处理后才能提交审批。"""

    field: str
    description: str
    hit_ids: tuple[str, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {"field": self.field, "description": self.description, "hit_ids": list(self.hit_ids)}


@dataclass(frozen=True)
class MergePreviewOutcome:
    """D04 结果。"""

    request_id: str
    preview_id: str
    preview_hash: str
    title: str
    answer_html: str
    summary: str
    source_hit_ids: tuple[str, ...] = ()
    conflicts: tuple[MergeConflict, ...] = ()
    warnings: tuple[str, ...] = ()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# §四  摘要计算（幂等与结果复用的依据）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _resource_payload(resource: SpotResource) -> dict[str, Any]:
    return {
        "resource_type": resource.resource_type,
        "resource_id": resource.resource_id,
        "record_id": resource.record_id,
        "content_hash": resource.content_hash,
        "publication_ids": list(resource.publication_ids),
        "entry_mode": resource.entry_mode,
    }


def _hit_from_payload(payload: dict[str, Any]) -> DuplicateHit:
    """快照里的 item 还原（快照是本侧自己写进去的，字段缺失按空值兜底）。"""
    resource = payload.get("resource") or {}
    return DuplicateHit(
        hit_id=str(payload.get("hit_id") or ""),
        hit_kind=str(payload.get("hit_kind") or HIT_DOC),
        resource=SpotResource(
            resource_type=str(resource.get("resource_type") or RESOURCE_DOCUMENT),
            resource_id=str(resource.get("resource_id") or ""),
            record_id=str(resource.get("record_id") or ""),
            content_hash=str(resource.get("content_hash") or ""),
            publication_ids=tuple(resource.get("publication_ids") or ()),
            entry_mode=str(resource.get("entry_mode") or "manual"),
        ),
        title=str(payload.get("title") or ""),
        snippet=str(payload.get("snippet") or ""),
        similarity=float(payload.get("similarity") or 0.0),
        reason=str(payload.get("reason") or ""),
        chunk_id=payload.get("chunk_id"),
        source_seq=payload.get("source_seq"),
        full_text=payload.get("full_text"),
    )


def scope_payload(scope: ScopeData) -> dict[str, Any]:
    """范围快照（落库与展示用）：比 ``scope_digest`` 多了 ``scope_id`` 便于溯源。"""
    return {
        "scope_id": scope.scope_id,
        "resources": [_resource_payload(resource) for resource in scope.resources],
        "drafts": [
            {
                "resource": _resource_payload(draft.resource),
                "title": draft.title,
                "summary": draft.summary or "",
                "content": draft.content,
                "chunks": [
                    {"seq": chunk.seq, "title": chunk.title or "", "content": chunk.content}
                    for chunk in sorted(draft.chunks, key=lambda item: item.seq)
                ],
            }
            for draft in scope.drafts
        ],
    }


def draft_digest(draft: SpotDraft) -> str:
    """草稿内容摘要：草稿不落库，是否「内容变了」只能靠随请求重传的正文比对。"""
    payload = {
        "identity": draft.resource.identity,
        "record_id": draft.resource.record_id,
        "content_hash": draft.resource.content_hash,
        "entry_mode": draft.resource.entry_mode,
        "publication_ids": sorted(draft.resource.publication_ids),
        "title": draft.title,
        "summary": draft.summary or "",
        "content": draft.content,
        "chunks": [[chunk.seq, chunk.title or "", chunk.content] for chunk in sorted(draft.chunks, key=lambda c: c.seq)],
    }
    return canonical_json_sha256(payload)


def scope_digest(scope: ScopeData, kinds: tuple[str, ...] | list[str], score_profile: str) -> str:
    """范围 + 内容 + 策略的联合摘要：任一变化都必须重建结果（§九）。"""
    payload = {
        "score_profile": score_profile,
        "kinds": sorted(kinds),
        "resources": sorted(
            (_resource_payload(resource) for resource in scope.resources), key=lambda row: row["resource_id"]
        ),
        "drafts": sorted(draft_digest(draft) for draft in scope.drafts),
    }
    return canonical_json_sha256(payload)


def content_digest(scope: ScopeData) -> str:
    """内容摘要：只看「比较了什么内容」，用于审计与排障（不含策略）。"""
    payload = {
        "resources": sorted(
            (
                {
                    "identity": resource.identity,
                    "record_id": resource.record_id,
                    "content_hash": resource.content_hash,
                }
                for resource in scope.resources
            ),
            key=lambda row: row["identity"],
        ),
        "drafts": sorted(draft_digest(draft) for draft in scope.drafts),
    }
    return canonical_json_sha256(payload)


def digest_token(value: str) -> str:
    """短摘要（hit_id 等稳定标识的原料）。"""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
