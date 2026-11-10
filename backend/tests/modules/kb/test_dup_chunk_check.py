"""D06 单篇知识切片查重：切片口径、去重计数与超限闸门。

锁的是三条契约约束（§D06），它们都不需要数据库：

1. ``total_chunks`` 是「正文非空白的切片总数」，而 ``duplicate_chunks`` 按 ``source_seq`` 去重
   ——同一片命中多篇文档只算一片，否则占比会超过 1；
2. ``matches`` 必须完整，超过上限回 ``RESULT_TOO_LARGE``（413）而不是截断后报成功；
3. ``candidate_resource`` 必须与 ``scope.resources`` 中一项**逐字段一致**，否则拒绝。

顺带锁投影的字段顺序：响应体要能直接与契约示例逐行对照。
"""

import pytest
from pydantic import ValidationError

from app.modules.kb.dup_result.detector import candidate_slices, chunk_check_matches
from app.modules.kb.dup_result.errors import DupInvalidRequestError, DupResultTooLargeError
from app.modules.kb.dup_result.schema import (
    CHUNK_CHECK_MAX_MATCHES,
    ChunkCheckOutcome,
    ChunkMatch,
    GovernanceItem,
    SpotChunk,
    SpotResource,
)
from app.modules_api.kb.duplicate.schema import ChunkCheckRequest

# 契约示例里的两段 VPN 正文：完全相同即相似度 1.0，越过 0.65 阈值
_VPN_APPLY = "通过服务台提交 VPN 申请，由责任人审核后开通账号。"
_VPN_LOGIN = "连接 VPN 前确认网络畅通，并使用已授权的企业账号登录。"


def _resource(
    identifier: str,
    *,
    record_id: str | None = None,
    content_hash: str = "hash",
) -> SpotResource:
    return SpotResource(
        resource_type="document",
        resource_id=identifier,
        record_id=record_id or f"rec-{identifier}",
        content_hash=content_hash,
        entry_mode="ai",
    )


def _item(identifier: str, chunks: list[tuple[int, str]], *, content: str | None = None) -> GovernanceItem:
    """构造一个参与比较的资源；``content`` 缺省按切片正文拼接（与 source 取内容的口径一致）。"""
    return GovernanceItem(
        resource=_resource(identifier),
        title=f"标题-{identifier}",
        content="\n".join(text for _, text in chunks) if content is None else content,
        chunks=tuple(SpotChunk(seq=seq, content=text) for seq, text in chunks),
    )


# ── 切片口径 ─────────────────────────────────────────────────────────


def test_candidate_slices_skips_blank_chunks() -> None:
    """空白切片不参与比较也不计入 ``total_chunks``：拿空串比相似度永远是 0，只会虚增分母。"""
    item = _item("doc-001", [(1, "正文"), (2, "   "), (3, "")])
    assert [chunk.seq for chunk in candidate_slices(item)] == [1]


def test_candidate_slices_falls_back_to_whole_content() -> None:
    """尚未切片的草稿把整篇正文当作唯一一片：否则 total_chunks=0，占比无从计算。"""
    item = _item("doc-001", [], content="整篇正文")
    assert [(chunk.seq, chunk.content) for chunk in candidate_slices(item)] == [(1, "整篇正文")]


def test_blank_candidate_is_rejected() -> None:
    """既无切片正文也无整篇正文时必须报错，不能返回「0 命中」冒充查过了。"""
    item = _item("doc-001", [(1, "  ")], content="  ")
    with pytest.raises(DupInvalidRequestError):
        chunk_check_matches(item, [])


# ── 命中与计数 ───────────────────────────────────────────────────────


def test_distinct_source_slices_are_counted_once() -> None:
    """``duplicate_chunks`` 按 ``source_seq`` 去重：两片分别命中两篇文档，结果是 2 不是 4。"""
    candidate = _item("doc-001", [(1, _VPN_APPLY), (2, _VPN_LOGIN), (3, "确认终端版本及设备登记信息。")])
    first = _item("doc-002", [(1, _VPN_APPLY), (2, _VPN_LOGIN)])
    second = _item("doc-003", [(1, _VPN_APPLY), (2, _VPN_LOGIN)])

    matches, total_chunks = chunk_check_matches(candidate, [first, second])
    outcome = ChunkCheckOutcome(
        request_id="req-1",
        candidate=candidate.resource,
        evaluated_resources=3,
        total_chunks=total_chunks,
        matches=matches,
    )

    assert total_chunks == 3
    assert len(matches) == 4
    assert outcome.duplicate_chunks == 2
    # 不做四舍五入：契约示例给的就是浮点原值
    assert outcome.duplicate_ratio == 2 / 3


def test_no_match_keeps_zero_ratio() -> None:
    """无命中时占比为 0，``matches`` 为空数组——这是「确实比过且不重复」，不是失败兜底。"""
    candidate = _item("doc-001", [(1, _VPN_APPLY)])
    other = _item("doc-002", [(1, "完全不相关的另一段内容，讲的是门禁卡补办流程。")])

    matches, total_chunks = chunk_check_matches(candidate, [other])
    outcome = ChunkCheckOutcome(
        request_id="req-1",
        candidate=candidate.resource,
        evaluated_resources=2,
        total_chunks=total_chunks,
        matches=matches,
    )

    assert matches == ()
    assert outcome.duplicate_chunks == 0
    assert outcome.duplicate_ratio == 0.0


def test_matches_are_order_stable() -> None:
    """遍历顺序固定（source_seq → target 身份 → target_seq）：targets 顺序变化不改变证据批次。"""
    candidate = _item("doc-001", [(1, _VPN_APPLY)])
    first = _item("doc-002", [(1, _VPN_APPLY)])
    second = _item("doc-003", [(1, _VPN_APPLY)])

    forward, _ = chunk_check_matches(candidate, [first, second])
    backward, _ = chunk_check_matches(candidate, [second, first])

    assert [match.to_payload() for match in forward] == [match.to_payload() for match in backward]
    assert [match.target.resource_id for match in forward] == ["doc-002", "doc-003"]


# ── 超限闸门 ─────────────────────────────────────────────────────────


def test_match_overflow_raises_instead_of_truncating() -> None:
    """命中数超过上限必须报错：截断后返回 200 等于把「还有没报出来的重复」藏起来。"""
    candidate = _item("doc-001", [(1, _VPN_APPLY)])
    target = _item("doc-002", [(seq, _VPN_APPLY) for seq in range(1, CHUNK_CHECK_MAX_MATCHES + 2)])

    with pytest.raises(DupResultTooLargeError):
        chunk_check_matches(candidate, [target])


def test_match_at_limit_is_accepted() -> None:
    """恰好等于上限仍然完整返回：边界值不能被 off-by-one 判成超限。"""
    candidate = _item("doc-001", [(1, _VPN_APPLY)])
    target = _item("doc-002", [(seq, _VPN_APPLY) for seq in range(1, CHUNK_CHECK_MAX_MATCHES + 1)])

    matches, _ = chunk_check_matches(candidate, [target])

    assert len(matches) == CHUNK_CHECK_MAX_MATCHES


# ── 响应投影 ─────────────────────────────────────────────────────────


def test_match_payload_follows_contract_field_order() -> None:
    """``matches[]`` 与 ``candidate_resource`` 的字段顺序与契约示例一致，便于逐行对照。"""
    payload = ChunkMatch(
        source_seq=1,
        target=_resource("doc-002"),
        target_seq=2,
        source_content=_VPN_APPLY,
        target_content=_VPN_APPLY,
        similarity=1.0,
    ).to_payload()

    assert list(payload) == [
        "source_seq",
        "target_resource",
        "target_seq",
        "source_content",
        "target_content",
        "similarity",
    ]
    assert list(payload["target_resource"]) == [
        "resource_type",
        "resource_id",
        "record_id",
        "content_hash",
        "publication_ids",
        "entry_mode",
    ]


# ── 请求一致性（契约层） ─────────────────────────────────────────────


def _request_payload(*, candidate: dict, resources: list[dict]) -> dict:
    return {
        "request_id": "req-chunk-check-001",
        "tenant_id": "gsi-default",
        "user_id": "42",
        "candidate_resource": candidate,
        "scope": {
            "scope_id": "scope-chunk-001",
            "issued_at": 1800000000000,
            "expires_at": 1800000120000,
            "resources": resources,
            "drafts": [],
        },
    }


def _raw_resource(identifier: str, *, record_id: str | None = None, publications: list[str] | None = None) -> dict:
    return {
        "resource_type": "document",
        "resource_id": identifier,
        "record_id": record_id or f"rec-{identifier}",
        "content_hash": "hash",
        "publication_ids": [] if publications is None else publications,
        "entry_mode": "ai",
    }


def test_request_accepts_candidate_listed_in_scope() -> None:
    """候选在清单内且逐字段一致 → 受理；``publication_ids`` 顺序不同不算不一致。"""
    payload = _request_payload(
        candidate=_raw_resource("doc-001", publications=["pub-b", "pub-a"]),
        resources=[_raw_resource("doc-001", publications=["pub-a", "pub-b"]), _raw_resource("doc-002")],
    )
    assert ChunkCheckRequest(**payload).candidate_resource.resource_id == "doc-001"


def test_request_rejects_candidate_outside_scope() -> None:
    """不在清单内属于越权检测：直接 400，不能「顺手多比一篇」。"""
    payload = _request_payload(
        candidate=_raw_resource("doc-999"),
        resources=[_raw_resource("doc-001"), _raw_resource("doc-002")],
    )
    with pytest.raises(ValidationError):
        ChunkCheckRequest(**payload)


def test_request_rejects_candidate_with_other_record_version() -> None:
    """只对上 ``resource_id`` 而 ``record_id`` 不同，等于拿另一个版本比，结论不成立。"""
    payload = _request_payload(
        candidate=_raw_resource("doc-001", record_id="rec-old"),
        resources=[_raw_resource("doc-001", record_id="rec-new")],
    )
    with pytest.raises(ValidationError):
        ChunkCheckRequest(**payload)
