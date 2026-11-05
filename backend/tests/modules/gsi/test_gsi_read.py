"""GSI 业务数据菜单（/gsi）：路由登记、时间口径、规则解析与组织树纠错。

不连库：这一层最容易出错的三处都是纯逻辑——
1. GSI 时间列是 ``timestamp(6)`` 且落库即业务本地时间，若走 AI 侧 UTC 换算会多 8 小时；
2. FAQ 生效规则优先读 publication 上的规则、为空才回落 record 快照，缺 ``invisibleTeams`` 即无效；
3. 组织树要能吞下悬挂 parent_id、自引用与互指成环三种脏数据，节点不能从视图里消失。
"""

from datetime import datetime

import pytest

from app import create_app
from app.modules_gsi.base import format_gsi_datetime
from app.modules_gsi.faq.faq_publication.service import resolve_access_rules
from app.modules_gsi.km.doc_publication.schema import SbpKmDocPublicationWithTeamOutSchema
from app.modules_gsi.sys.audience_team_member.schema import (
    SbpSysAudienceTeamMemberWithTeamOutSchema,
)
from app.modules_gsi.sys.org.model import SbpSysOrgModel
from app.modules_gsi.sys.org.service import SbpSysOrgService

GSI_READ_PATHS: set[str] = {
    "/api/v1/gsi/sys/org/list",
    "/api/v1/gsi/sys/org/detail/{id}",
    "/api/v1/gsi/sys/org/tree",
    "/api/v1/gsi/sys/org-member/list",
    "/api/v1/gsi/sys/org-member/page-with-org",
    "/api/v1/gsi/sys/audience-team/list",
    "/api/v1/gsi/sys/audience-team/detail/{id}",
    "/api/v1/gsi/sys/audience-team-member/list",
    "/api/v1/gsi/sys/audience-team-member/page-with-team",
    "/api/v1/gsi/sys/audience-team-member/user-teams",
    "/api/v1/gsi/sys/user/list",
    "/api/v1/gsi/sys/user/detail/{id}",
    "/api/v1/gsi/km/doc/list",
    "/api/v1/gsi/km/doc/detail/{id}",
    "/api/v1/gsi/km/doc-publication/list",
    "/api/v1/gsi/km/doc-publication/page-with-team",
    "/api/v1/gsi/km/doc-record/list",
    "/api/v1/gsi/km/doc-record/detail/{id}",
    "/api/v1/gsi/faq/list",
    "/api/v1/gsi/faq/detail/{id}",
    "/api/v1/gsi/faq/publication/list",
    "/api/v1/gsi/faq/record/list",
    "/api/v1/gsi/faq/record/detail/{id}",
}


def test_gsi_read_routes_are_registered() -> None:
    """契约 §四的 11 张表都有对应只读入口，包括本次新增的联表与 FAQ 三类。"""
    paths = set(create_app().openapi()["paths"])
    assert GSI_READ_PATHS <= paths


# ── 时间口径 ────────────────────────────────────────────────────────


def test_format_gsi_datetime_keeps_wall_clock() -> None:
    """naive 本地时间原样输出：不能被当成 UTC 再加 8 小时。"""
    assert format_gsi_datetime(datetime(2026, 11, 5, 9, 30, 0)) == "2026-11-05 09:30:00"
    assert format_gsi_datetime(None) is None


# ── FAQ 生效规则解析 ────────────────────────────────────────────────

VALID_RULES = '{"invisibleTeams": ["team-a", "", " team-b "], "expireAt": "2026-12-31"}'


def test_resolve_access_rules_prefers_publication() -> None:
    """publication 上有规则就只认它，不再叠加 record 快照。"""
    result = resolve_access_rules(VALID_RULES, '{"invisibleTeams": ["team-z"]}')
    assert result["rules_source"] == "publication"
    assert result["rules_valid"] is True
    # 空白项被清掉、首尾空格被 trim：黑名单是精确匹配，带空格等于没生效
    assert result["invisible_teams"] == ["team-a", "team-b"]
    assert result["rules_error"] is None


def test_resolve_access_rules_falls_back_to_record() -> None:
    """publication 为空串时回落 record 快照。"""
    result = resolve_access_rules("  ", VALID_RULES)
    assert result["rules_source"] == "record"
    assert result["rules_valid"] is True
    assert result["invisible_teams"] == ["team-a", "team-b"]


@pytest.mark.parametrize(
    ("publication_raw", "snapshot_raw", "reason"),
    [
        (None, None, "生效规则为空"),
        ("{not-json", None, "解析失败"),
        ("[]", None, "不是 JSON 对象"),
        ('{"expireAt": "2026-12-31"}', None, "invisibleTeams"),
        ('{"invisibleTeams": "team-a"}', None, "invisibleTeams"),
    ],
)
def test_resolve_access_rules_rejects_invalid(
    publication_raw: str | None, snapshot_raw: str | None, reason: str
) -> None:
    """规则缺失/非法/黑名单不是数组一律判无效，且给出可读原因。"""
    result = resolve_access_rules(publication_raw, snapshot_raw)
    assert result["rules_valid"] is False
    assert result["invisible_teams"] == []
    assert reason in (result["rules_error"] or "")


# ── 联表派生标记 ────────────────────────────────────────────────────


def test_member_with_team_derives_valid_flags() -> None:
    """成员标识页的有效性在模型内派生，service 与前端都不再各算一遍。"""
    enabled = SbpSysAudienceTeamMemberWithTeamOutSchema.model_validate(
        {
            "team_id": 7,
            "group_code": "it",
            "team_status": "1",
            "team_deleted": 0,
            "user_id": 42,
            "user_status": "1",
            "user_deleted": 0,
        }
    )
    assert (enabled.team_valid, enabled.user_exists, enabled.user_valid) == (True, True, True)

    # 团队被停用、用户在 sbp_sys_user 里不存在：关系行还在，但两边都无效
    disabled = SbpSysAudienceTeamMemberWithTeamOutSchema.model_validate(
        {"team_id": 8, "team_status": "0", "team_deleted": 0, "user_id": 43}
    )
    assert (disabled.team_valid, disabled.user_exists, disabled.user_valid) == (False, False, False)


def test_doc_publication_distinguishes_unmatched_team() -> None:
    """team 编码未命中受众团队表时必须显式暴露，而不是当普通行展示。"""
    unmatched = SbpKmDocPublicationWithTeamOutSchema.model_validate(
        {
            "id": "pub-1",
            "doc_id": "doc-1",
            "team": "IT团队",
            "record_id": "rec-1",
            "index_status": "succeeded",
            "updated_at": "2026-11-05 09:30:00",
        }
    )
    assert (unmatched.team_matched, unmatched.team_valid) == (False, False)

    matched = SbpKmDocPublicationWithTeamOutSchema.model_validate(
        {
            "id": "pub-2",
            "doc_id": "doc-1",
            "team": "it",
            "team_status": "1",
            "team_deleted": 0,
            "record_id": "rec-1",
            "index_status": "succeeded",
            "updated_at": "2026-11-05 09:30:00",
        }
    )
    assert (matched.team_matched, matched.team_valid) == (True, True)


# ── 组织树容错 ──────────────────────────────────────────────────────


def _org(node_id: int, parent_id: int) -> SbpSysOrgModel:
    """内存里构造组织节点（不需要 session）。"""
    return SbpSysOrgModel(
        id=node_id,
        parent_id=parent_id,
        name=f"组织{node_id}",
        code=f"ORG{node_id}",
        type="dept",
        category="business",
        source_org_id=None,
        status="1",
        deleted=0,
    )


def _flatten(nodes) -> list[int]:
    ids: list[int] = []
    for node in nodes:
        ids.append(node.id)
        ids.extend(_flatten(node.children))
    return ids


def test_build_tree_keeps_dirty_nodes_visible() -> None:
    """悬挂 parent、自引用、A↔B 环都必须留在树里，且每个节点只出现一次。"""
    nodes = [
        _org(1, 0),
        _org(2, 1),
        _org(3, 999),  # parent 不存在
        _org(4, 4),  # 自引用
        _org(5, 6),  # 与 6 互指成环
        _org(6, 5),
    ]
    forest = SbpSysOrgService._build_tree(nodes)

    root_ids = [node.id for node in forest]
    assert 3 in root_ids  # 悬挂按根处理
    assert 4 in root_ids  # 自引用按根处理
    assert sorted(_flatten(forest)) == [1, 2, 3, 4, 5, 6]
    # 正常层级仍然成立：2 挂在 1 下面
    node1 = next(node for node in forest if node.id == 1)
    assert [child.id for child in node1.children] == [2]
