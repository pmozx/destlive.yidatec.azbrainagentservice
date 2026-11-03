"""重复检测算法（kb 基座帮助层，纯函数）：不查库、不写库、不感知 HTTP。

为什么不用向量做重复判定：向量依赖不可用时只能返回「未发现重复」，而这正是 §九 明令
禁止的失败表现（失败不能显示「未发现重复」）。这里改用可复现的字面口径：

1. 归一化：NFKC + 小写 + 去空白标点，消除标点/全半角带来的假差异；
2. 相似度：字符 2-gram 的 Dice 系数与包含度的加权（短标题也能拉开差距）；
3. 分组：规范化标题分桶 → ``doc_exact``；倒排索引找候选对 + 并查集连通分量 → ``doc_similar``；
   FAQ 按 ``entry_mode`` 分 ``faq_ai`` / ``faq_manual``；切片按正文摘要精确分桶 → ``chunk``。

所有标识（``hit_id`` / ``group_id``）都由内容身份确定性推导：同一次输入必须得到同一批 ID，
否则翻页、详情、预览三步对不上同一批对象。
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterable, Sequence

from .schema import (
    COMPARE_TEXT_LIMIT,
    DUP_KINDS,
    FULL_TEXT_LIMIT,
    HIT_DOC,
    KIND_DOC_EXACT,
    KIND_DOC_SIMILAR,
    KIND_FAQ_AI,
    KIND_FAQ_MANUAL,
    KIND_HIT,
    KIND_CHUNK,
    MAX_MATCHES,
    METHOD_CHUNK_EXACT,
    METHOD_CONTENT_SIMILARITY,
    METHOD_TITLE_EXACT,
    METHOD_TITLE_SIMILARITY,
    RESOURCE_DOCUMENT,
    RESOURCE_FAQ,
    SIMILARITY_THRESHOLD,
    SNIPPET_LIMIT,
    DuplicateGroup,
    DuplicateHit,
    GovernanceItem,
    GroupEvidence,
    SpotChunk,
)

# 分组成员 = (所属对象, 切片)；切片为 None 表示按整篇（doc/faq）参与比较
Member = tuple[GovernanceItem, SpotChunk | None]

_NON_WORD_RE = re.compile(r"[\W_]+", re.UNICODE)
_SPACE_RE = re.compile(r"\s+")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# §一  文本归一与相似度
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def normalize_text(value: str | None) -> str:
    """NFKC + 小写 + 去空白标点：只留下文字与数字。"""
    text = unicodedata.normalize("NFKC", value or "").lower()
    return _NON_WORD_RE.sub("", text)


def _shingles(text: str, size: int = 2) -> set[str]:
    if not text:
        return set()
    if len(text) <= size:
        return {text}
    return {text[index : index + size] for index in range(len(text) - size + 1)}


def text_similarity(left: str | None, right: str | None) -> float:
    """0–1 字面相似度：Dice 主导（整体重合），包含度兜底（短标题包含长标题片段）。"""
    left_grams = _shingles(normalize_text(left))
    right_grams = _shingles(normalize_text(right))
    if not left_grams or not right_grams:
        return 0.0
    overlap = len(left_grams & right_grams)
    dice = 2 * overlap / (len(left_grams) + len(right_grams))
    containment = overlap / min(len(left_grams), len(right_grams))
    return round(0.6 * dice + 0.4 * containment, 4)


def clip_text(value: str | None, limit: int = SNIPPET_LIMIT) -> str:
    """折叠空白后截断：``snippet`` 与 ``evidence`` 的引用都必须取自同一份可比对文本。"""
    collapsed = _SPACE_RE.sub(" ", value or "").strip()
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[:limit] + "…"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# §二  成员投影
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _member_seed(member: Member) -> str:
    """成员身份种子：整篇取资源身份，切片再拼切片身份（正式 ID 优先，其次草稿序号）。"""
    item, chunk = member
    if chunk is None:
        return item.identity
    if chunk.chunk_id:
        return f"{item.identity}#{chunk.chunk_id}"
    return f"{item.identity}#seq:{chunk.seq}"


def _member_title(member: Member) -> str:
    item, chunk = member
    if chunk is None:
        return item.title
    return chunk.title or item.title


def _member_text(member: Member) -> str:
    item, chunk = member
    return chunk.content if chunk is not None else item.content


def _compare_text(member: Member) -> str:
    """参与相似度计算的文本：标题 + 正文（标题权重天然更高，因为它在正文之前出现）。"""
    if member[1] is not None:
        return _member_text(member)[:COMPARE_TEXT_LIMIT]
    return f"{_member_title(member)}\n{_member_text(member)}"[:COMPARE_TEXT_LIMIT]


def _member_full_text(member: Member) -> str:
    item, chunk = member
    if chunk is not None:
        return chunk.content[:FULL_TEXT_LIMIT]
    return f"{item.title}：{item.content}"[:FULL_TEXT_LIMIT]


def _pick_keep(members: Sequence[Member]) -> Member:
    """确定性推荐主项：正式内容优先于草稿 → 创建更早者 → 身份字典序最小。"""
    def sort_key(member: Member) -> tuple[int, float, str]:
        item = member[0]
        created = item.created_time.timestamp() if item.created_time else float("inf")
        return (1 if item.is_draft else 0, created, _member_seed(member))

    return sorted(members, key=sort_key)[0]


def _hit_reason(kind: str, method: str, *, is_keep: bool) -> str:
    """中文判断依据（契约要求必返，且不能写成「相似度 0.9」这种没有信息量的句子）。"""
    if kind in (KIND_DOC_EXACT,):
        return "规范化标题完全相同，需人工确认是否为同一知识。" if not is_keep else "作为建议保留项（标题最早出现）。"
    if kind == KIND_CHUNK:
        return "切片正文完全一致，可只保留一份。" if not is_keep else "作为建议保留项（切片最早出现）。"
    if kind in (KIND_FAQ_AI, KIND_FAQ_MANUAL):
        if is_keep:
            return "作为建议保留项（录入时间更早）。"
        return "问答标题与答案高度重合，需人工确认适用场景。"
    if method == METHOD_TITLE_SIMILARITY:
        return "标题相近，正文需人工比对差异。" if not is_keep else "作为建议保留项（标题更早出现）。"
    return "正文内容高度重合，需人工比对差异。" if not is_keep else "作为建议保留项（创建时间更早）。"


def _to_hit(*, kind: str, member: Member, similarity: float, is_keep: bool) -> DuplicateHit:
    seed = _member_seed(member)
    item, chunk = member
    chunk_id: str | None = None
    source_seq: int | None = None
    if chunk is not None:
        if chunk.chunk_id:
            chunk_id = chunk.chunk_id
        else:
            # 未入库草稿：AI 临时分配检测 ID，并回传 seq 供 GSI 定位（不得用于正式写入）
            chunk_id = "tmp-chunk-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]
            source_seq = chunk.seq
    method = _group_method(kind)
    return DuplicateHit(
        hit_id=_hit_id_of(member),
        hit_kind=KIND_HIT.get(kind, HIT_DOC),
        resource=item.resource,
        title=_member_title(member),
        snippet=clip_text(_member_text(member)),
        similarity=similarity,
        reason=_hit_reason(kind, method, is_keep=is_keep),
        chunk_id=chunk_id,
        source_seq=source_seq,
        full_text=_member_full_text(member),
    )


def _group_method(kind: str) -> str:
    return {
        KIND_DOC_EXACT: METHOD_TITLE_EXACT,
        KIND_CHUNK: METHOD_CHUNK_EXACT,
    }.get(kind, METHOD_TITLE_SIMILARITY)


def _pair_similarity(keep: Member, other: Member) -> tuple[float, str]:
    """整篇比较：标题相似度与正文相似度取高者，并回报实际生效的方法版本。"""
    title_score = text_similarity(_member_title(keep), _member_title(other))
    content_score = text_similarity(_compare_text(keep), _compare_text(other))
    if title_score >= content_score:
        return title_score, METHOD_TITLE_SIMILARITY
    return content_score, METHOD_CONTENT_SIMILARITY


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# §三  分组构造
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _compose_group(kind: str, members: Sequence[Member], pair) -> DuplicateGroup | None:
    """把一组相关成员固化成契约分组：``similarity`` 取「主项 vs 各成员」的最小值。"""
    if len(members) < 2:
        return None
    keep = _pick_keep(members)
    scores: dict[str, tuple[float, str]] = {}
    for member in members:
        if member == keep:
            continue
        scores[_member_seed(member)] = pair(keep, member)
    if not scores:
        return None
    # 传递关联不等于两两相同：取最弱的一对作为整组相似度，方法版本也随之取该对的
    similarity, method = min(scores.values(), key=lambda row: row[0])
    keep_hit_id = _hit_id_of(keep)
    hits = tuple(
        _to_hit(
            kind=kind,
            member=member,
            similarity=similarity if member == keep else scores[_member_seed(member)][0],
            is_keep=member == keep,
        )
        for member in members
    )
    return DuplicateGroup(
        group_id=build_group_key(kind, [_member_seed(member) for member in members]),
        kind=kind,
        title=_member_title(keep),
        similarity=similarity,
        score_method=method,
        reason=_group_reason(kind, method),
        items=hits,
        suggested_keep_id=keep_hit_id,
    )


def _hit_id_of(member: Member) -> str:
    return "hit-" + hashlib.sha256(_member_seed(member).encode("utf-8")).hexdigest()[:12]


def _group_reason(kind: str, method: str) -> str:
    if kind == KIND_DOC_EXACT:
        return "规范化标题相同（非全文相同），建议合并或补充区分信息。"
    if kind == KIND_CHUNK:
        return "切片正文完全一致，建议移除重复切片。"
    if kind in (KIND_FAQ_AI, KIND_FAQ_MANUAL):
        return "问答内容重合，建议确认是否合并答案。"
    if method == METHOD_CONTENT_SIMILARITY:
        return "文档正文高度重合，建议确认是否同一知识。"
    return "文档标题相近，建议确认是否同一知识。"


def build_group_key(kind: str, seeds: Iterable[str]) -> str:
    """稳定分组键：类型 + 排序后的成员身份摘要；成员身份变化即产生新键。"""
    seed = kind + "|" + "|".join(sorted(set(seeds)))
    return "dup-group-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def _union_find_components(count: int, edges: Iterable[tuple[int, int]]) -> list[list[int]]:
    parent = list(range(count))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for left, right in edges:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    components: dict[int, list[int]] = {}
    for index in range(count):
        components.setdefault(find(index), []).append(index)
    return [sorted(indices) for indices in components.values()]


def _similarity_components(members: Sequence[Member]) -> list[list[Member]]:
    """倒排索引找候选对 → 阈值过滤 → 连通分量（避免 O(n²) 全量比较）。"""
    grams_per_member = [_shingles(normalize_text(_compare_text(member))) for member in members]
    buckets: dict[str, list[int]] = {}
    for index, grams in enumerate(grams_per_member):
        for gram in grams:
            buckets.setdefault(gram, []).append(index)

    edges: list[tuple[int, int]] = []
    for index, grams in enumerate(grams_per_member):
        candidates: set[int] = set()
        for gram in grams:
            candidates.update(buckets.get(gram, ()))
        for other in candidates:
            if other <= index:
                continue
            score, _ = _pair_similarity(members[index], members[other])
            if score >= SIMILARITY_THRESHOLD:
                edges.append((index, other))

    return [[members[i] for i in indices] for indices in _union_find_components(len(members), edges)]


def _document_groups(items: Sequence[GovernanceItem], wanted: set[str]) -> list[DuplicateGroup]:
    documents = [item for item in items if item.resource.resource_type == RESOURCE_DOCUMENT]
    groups: list[DuplicateGroup] = []
    claimed: set[str] = set()

    if KIND_DOC_EXACT in wanted:
        buckets: dict[str, list[GovernanceItem]] = {}
        for item in documents:
            key = normalize_text(item.title)
            if key:
                buckets.setdefault(key, []).append(item)
        for members in buckets.values():
            group = _compose_group(
                KIND_DOC_EXACT, [(item, None) for item in members], lambda keep, other: (1.0, METHOD_TITLE_EXACT)
            )
            if group is not None:
                groups.append(group)
                claimed.update(item.identity for item in members)

    if KIND_DOC_SIMILAR in wanted:
        # 已进入 exact 分组的文档不再参与相似分组：同一条知识只归一类，避免页面重复计数
        rest = [item for item in documents if item.identity not in claimed]
        for component in _similarity_components([(item, None) for item in rest]):
            group = _compose_group(KIND_DOC_SIMILAR, component, _pair_similarity)
            if group is not None:
                groups.append(group)
    return groups


def _faq_groups(items: Sequence[GovernanceItem], wanted: set[str]) -> list[DuplicateGroup]:
    """FAQ 分组按录入来源分开：``faq_ai`` 与 ``faq_manual`` 是不同治理口径。"""
    groups: list[DuplicateGroup] = []
    for entry_mode, kind in (("ai", KIND_FAQ_AI), ("manual", KIND_FAQ_MANUAL)):
        if kind not in wanted:
            continue
        members = [
            (item, None)
            for item in items
            if item.resource.resource_type == RESOURCE_FAQ and (item.resource.entry_mode or "manual") == entry_mode
        ]
        for component in _similarity_components(members):
            group = _compose_group(kind, component, _pair_similarity)
            if group is not None:
                groups.append(group)
    return groups


def _chunk_groups(items: Sequence[GovernanceItem]) -> list[DuplicateGroup]:
    """切片按归一化正文摘要精确分桶：内容一致才算重复，不做模糊匹配。"""
    buckets: dict[str, list[Member]] = {}
    for item in items:
        for chunk in item.chunks:
            key = hashlib.sha256(normalize_text(chunk.content).encode("utf-8")).hexdigest()
            buckets.setdefault(key, []).append((item, chunk))
    groups: list[DuplicateGroup] = []
    for members in buckets.values():
        group = _compose_group(KIND_CHUNK, members, lambda keep, other: (1.0, METHOD_CHUNK_EXACT))
        if group is not None:
            groups.append(group)
    return groups


def detect_groups(items: Sequence[GovernanceItem], kinds: Sequence[str] = DUP_KINDS) -> list[DuplicateGroup]:
    """范围内全量分组：按 ``kinds`` 选择性启用五类检测，输出顺序稳定（同类内相似度高者在前）。"""
    wanted = set(kinds) if kinds else set(DUP_KINDS)
    groups: list[DuplicateGroup] = []
    if wanted & {KIND_DOC_EXACT, KIND_DOC_SIMILAR}:
        groups.extend(_document_groups(items, wanted))
    if wanted & {KIND_FAQ_AI, KIND_FAQ_MANUAL}:
        groups.extend(_faq_groups(items, wanted))
    if KIND_CHUNK in wanted:
        groups.extend(_chunk_groups(items))
    groups.sort(key=lambda group: (group.kind, -group.similarity, group.group_id))
    return groups


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# §四  D01 保存前查重
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _check_kind(item: GovernanceItem, candidate: GovernanceItem, wanted: set[str]) -> str | None:
    """候选与某个范围内对象之间的治理类型（None = 本次 kinds 未启用该口径）。"""
    if item.resource.resource_type != candidate.resource.resource_type:
        return None
    same_title = bool(normalize_text(item.title)) and normalize_text(item.title) == normalize_text(candidate.title)
    if item.resource.resource_type == RESOURCE_DOCUMENT:
        if same_title and KIND_DOC_EXACT in wanted:
            return KIND_DOC_EXACT
        return KIND_DOC_SIMILAR if KIND_DOC_SIMILAR in wanted else None
    kind = KIND_FAQ_AI if (item.resource.entry_mode or "manual") == "ai" else KIND_FAQ_MANUAL
    return kind if kind in wanted else None


def check_candidate(
    candidate: GovernanceItem,
    items: Sequence[GovernanceItem],
    kinds: Sequence[str] = DUP_KINDS,
) -> tuple[tuple[DuplicateHit, ...], float]:
    """候选 vs 授权范围：排除相同 ``resource_type + resource_id`` 的旧版本，避免自己与自己重复。

    返回 ``(命中列表, 最高相似度)``；``is_duplicate`` 由调用方按阈值判定。
    """
    wanted = set(kinds) if kinds else set(DUP_KINDS)
    matches: list[DuplicateHit] = []
    seen: set[str] = set()

    for item in items:
        if item.resource.resource_type != candidate.resource.resource_type:
            continue
        if item.resource.resource_id == candidate.resource.resource_id:
            continue
        kind = _check_kind(item, candidate, wanted)
        if kind is None:
            continue
        score, method = _pair_similarity((candidate, None), (item, None))
        if score < SIMILARITY_THRESHOLD:
            continue
        hit = _to_hit(kind=kind, member=(item, None), similarity=score, is_keep=False)
        if hit.hit_id in seen:
            continue
        seen.add(hit.hit_id)
        matches.append(hit)

    if KIND_CHUNK in wanted:
        candidate_hashes = {
            hashlib.sha256(normalize_text(chunk.content).encode("utf-8")).hexdigest()
            for chunk in candidate.chunks
            if normalize_text(chunk.content)
        }
        if candidate_hashes:
            for item in items:
                if item.resource.resource_id == candidate.resource.resource_id:
                    continue
                for chunk in item.chunks:
                    digest = hashlib.sha256(normalize_text(chunk.content).encode("utf-8")).hexdigest()
                    if digest not in candidate_hashes:
                        continue
                    hit = _to_hit(kind=KIND_CHUNK, member=(item, chunk), similarity=1.0, is_keep=False)
                    if hit.hit_id in seen:
                        continue
                    seen.add(hit.hit_id)
                    matches.append(hit)

    matches.sort(key=lambda hit: (-hit.similarity, hit.hit_id))
    top = tuple(matches[:MAX_MATCHES])
    similarity = max((hit.similarity for hit in top), default=0.0)
    return top, similarity


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# §五  对比依据
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# 契约 evidence.basis 只有 5 个取值；本侧是字面比较，映射到最贴近的一档
_BASIS_BY_METHOD: dict[str, str] = {
    METHOD_TITLE_EXACT: "title_exact",
    METHOD_TITLE_SIMILARITY: "title_similarity",
    METHOD_CONTENT_SIMILARITY: "content_exact",
    METHOD_CHUNK_EXACT: "content_exact",
}


def _evidence_basis(kind: str, method: str) -> str:
    if kind in (KIND_FAQ_AI, KIND_FAQ_MANUAL):
        return "answer_overlap"
    return _BASIS_BY_METHOD.get(method, "title_similarity")


def group_evidence(group: DuplicateGroup) -> tuple[GroupEvidence, ...]:
    """主项与其余成员逐对给出原文引用；引用取自 ``full_text``，保证「引用能被找到」。"""
    if len(group.items) < 2:
        return ()
    keep = next((hit for hit in group.items if hit.hit_id == group.suggested_keep_id), group.items[0])
    basis = _evidence_basis(group.kind, group.score_method)
    use_title = basis.startswith("title")
    evidences: list[GroupEvidence] = []
    for hit in group.items:
        if hit.hit_id == keep.hit_id:
            continue
        evidences.append(
            GroupEvidence(
                left_hit_id=keep.hit_id,
                right_hit_id=hit.hit_id,
                basis=basis,
                left_quote=clip_text(keep.title if use_title else keep.full_text or keep.snippet, 80),
                right_quote=clip_text(hit.title if use_title else hit.full_text or hit.snippet, 80),
                explanation=f"{group.reason}该成员与主项相似度 {hit.similarity:.2f}。",
            )
        )
    return tuple(evidences)


__all__ = [
    "Member",
    "build_group_key",
    "check_candidate",
    "clip_text",
    "detect_groups",
    "group_evidence",
    "normalize_text",
    "text_similarity",
]
