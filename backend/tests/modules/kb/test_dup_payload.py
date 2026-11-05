"""重复治理请求投影（#16–#19）：容量上限与策略版本校验的下沉点。

这两条规则原先只写在 `/v1` 接入层，管理端等其它调用方等于没有；现在判定在
``app.modules.kb.dup_result.schema``，所以直接锁领域层的纯函数，不需要数据库。
"""

import pytest

from app.config.setting import settings
from app.modules.kb.dup_result.errors import DupInvalidRequestError, DupScopeTooLargeError
from app.modules.kb.dup_result.schema import (
    SCORE_PROFILE,
    GovernanceItem,
    ScopeData,
    SpotChunk,
    assert_score_profile,
)


def _resource(identifier: str) -> dict:
    return {
        "resource_type": "document",
        "resource_id": identifier,
        "record_id": f"rec-{identifier}",
        "content_hash": "hash",
    }


def test_scope_projection_keeps_resources_and_drafts() -> None:
    """请求 scope → 领域范围：资源身份保留，草稿的 ``entry_mode`` 缺省为 manual。"""
    scope = ScopeData.from_payload(
        {
            "scope_id": "scope-1",
            "resources": [_resource("a")],
            "drafts": [{"resource": _resource("b"), "title": "标题", "content": "正文"}],
        }
    )
    assert scope.scope_id == "scope-1"
    assert [item.identity for item in scope.resources] == ["document:a"]
    assert scope.drafts[0].resource.entry_mode == "manual"


def test_scope_projection_rejects_oversized_scope() -> None:
    """超过容量上限必须报错而不是截断：截断等于「少比了一部分内容还报成功」。"""
    payload = {
        "scope_id": "scope-1",
        "resources": [_resource(str(index)) for index in range(settings.DUP_MAX_SCOPE_ITEMS + 1)],
    }
    with pytest.raises(DupScopeTooLargeError):
        ScopeData.from_payload(payload)


def test_scope_projection_accepts_scope_at_limit() -> None:
    """恰好等于上限仍然受理：边界值不能被 off-by-one 拒掉。"""
    payload = {
        "scope_id": "scope-1",
        "resources": [_resource(str(index)) for index in range(settings.DUP_MAX_SCOPE_ITEMS)],
    }
    assert ScopeData.from_payload(payload).requested_resources == settings.DUP_MAX_SCOPE_ITEMS


def test_score_profile_must_match_implementation() -> None:
    """策略版本必须与实现一致：静默按 v1 算再把 v2 回给调用方，相似度不可解释。"""
    assert_score_profile(SCORE_PROFILE)
    with pytest.raises(DupInvalidRequestError):
        assert_score_profile("gsi-duplicate-v2")


def test_candidate_projection_is_always_draft() -> None:
    """候选内容只参与本次比较：投影后 ``is_draft=True``，不落任何正式表。"""
    item = GovernanceItem.from_payload({"resource": _resource("a"), "title": "标题", "content": "正文"})
    assert item.is_draft is True
    assert item.chunks == ()


def test_chunk_projection_keeps_missing_chunk_id() -> None:
    """未入库草稿切片没有 ``chunk_id``：投影保留空值，由领域层判定为草稿。"""
    assert SpotChunk.from_payload({"seq": 1, "content": "正文"}).chunk_id is None
