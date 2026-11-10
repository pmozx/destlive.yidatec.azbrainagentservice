"""GSI 业务数据菜单（/gsi）：路由登记、时间口径、规则解析与组织树纠错。

不连库：这一层最容易出错的三处都是纯逻辑——
1. GSI 时间列是 ``timestamp(6)`` 且落库即业务本地时间，若走 AI 侧 UTC 换算会多 8 小时；
2. FAQ 生效规则优先读 publication 上的规则、为空才回落 record 快照，缺 ``invisibleTeams`` 即无效；
3. 组织树要能吞下悬挂 parent_id、自引用与互指成环三种脏数据，节点不能从视图里消失。
"""

from datetime import date, datetime

import pytest

from app import create_app
from app.modules_gsi.base import format_gsi_datetime
from app.modules_gsi.faq.faq_publication.service import resolve_access_rules
from app.modules_gsi.km.doc_publication.schema import SbpKmDocPublicationWithTeamOutSchema
from app.modules_gsi.km.doc_publication.service import is_rule_visible, resolve_effective_rule
from app.modules_gsi.sys.audience_team_member.schema import (
    SbpSysAudienceTeamMemberWithTeamOutSchema,
)
from app.modules_gsi.sys.org.model import SbpSysOrgModel
from app.modules_gsi.sys.org.service import SbpSysOrgService
from app.modules_gsi.sys.org_member.schema import SbpSysOrgMemberWithOrgOutSchema
from app.modules_gsi.sys.org_member.service import SbpSysOrgMemberService
from app.modules_gsi.sys.user.model import SbpSysUserModel
from app.modules_gsi.sys.user.schema import SbpSysUserQueryParam

GSI_READ_PATHS: set[str] = {
    "/api/v1/gsi/sys/org/list",
    "/api/v1/gsi/sys/org/detail/{id}",
    "/api/v1/gsi/sys/org/tree",
    "/api/v1/gsi/sys/org-member/list",
    "/api/v1/gsi/sys/org-member/page-with-org",
    "/api/v1/gsi/sys/org-member/user-orgs",
    "/api/v1/gsi/sys/audience-team/list",
    "/api/v1/gsi/sys/audience-team/detail/{id}",
    "/api/v1/gsi/sys/audience-team-member/list",
    "/api/v1/gsi/sys/audience-team-member/page-with-team",
    "/api/v1/gsi/sys/audience-team-member/user-teams",
    "/api/v1/gsi/sys/user/list",
    "/api/v1/gsi/sys/user/detail/{id}",
    "/api/v1/gsi/sys/rank/list",
    "/api/v1/gsi/sys/rank/detail/{id}",
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
    """契约 §四的表都有只读入口，含联表视图、按用户反查与职级引用表。"""
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


# ── 文档生效规则（消费授权第 4 步） ──────────────────────────────

DOC_SNAPSHOT = '{"invisibleTeams": ["team-a", "", " team-b "], "expireAt": "2026-12-31"}'
TODAY = date(2026, 11, 10)


def test_resolve_effective_rule_reads_blacklist_and_expiry() -> None:
    """黑名单空白项被清掉、首尾空格 trim：黑名单是精确匹配，带空格等于没生效。"""
    rule = resolve_effective_rule(DOC_SNAPSHOT)
    assert rule.valid is True
    assert rule.invisible_teams == ("team-a", "team-b")
    assert rule.expire_at == date(2026, 12, 31)


def test_resolve_effective_rule_takes_date_part_of_iso_datetime() -> None:
    """``expireAt`` 也有带时区的 ISO 8601；只比业务日期，不做时区换算。"""
    rule = resolve_effective_rule('{"invisibleTeams": [], "expireAt": "2026-12-31T09:00:00+08:00"}')
    assert rule.expire_at == date(2026, 12, 31)


def test_resolve_effective_rule_without_expiry_is_open_ended() -> None:
    """没填有效期 = 长期有效：不能自己编一个到期日把内容提前藏起来。"""
    rule = resolve_effective_rule('{"invisibleTeams": []}')
    assert rule.valid is True
    assert rule.expire_at is None


@pytest.mark.parametrize(
    ("snapshot_json", "reason"),
    [
        (None, "生效快照为空"),
        ("  ", "生效快照为空"),
        ("{not-json", "解析失败"),
        ("[]", "不是 JSON 对象"),
        ('{"expireAt": "2026-12-31"}', "invisibleTeams"),
        ('{"invisibleTeams": "team-a"}', "invisibleTeams"),
        ('{"invisibleTeams": [], "expireAt": "明年"}', "expireAt"),
    ],
)
def test_resolve_effective_rule_rejects_unusable_snapshot(snapshot_json: str | None, reason: str) -> None:
    """拿不到可信规则就不能开：缺黑名单、不是对象、到期日认不出一律判无效。"""
    rule = resolve_effective_rule(snapshot_json)
    assert rule.valid is False
    assert reason in (rule.reason or "")


def test_rule_visibility_rejects_blacklisted_team() -> None:
    """命中用户**任一**团队的 blacklist → 不可见（排除优先，与是否有其它团队无关）。"""
    rule = resolve_effective_rule(DOC_SNAPSHOT)
    assert is_rule_visible(rule, user_teams={"it", "team-b"}, today=TODAY) is False
    assert is_rule_visible(rule, user_teams={"it", "hr"}, today=TODAY) is True


def test_rule_visibility_rejects_expired_knowledge() -> None:
    """``expireAt`` 早于当天拒绝，等于当天仍可用。"""
    expired = resolve_effective_rule('{"invisibleTeams": [], "expireAt": "2026-11-09"}')
    last_day = resolve_effective_rule('{"invisibleTeams": [], "expireAt": "2026-11-10"}')
    assert is_rule_visible(expired, user_teams={"it"}, today=TODAY) is False
    assert is_rule_visible(last_day, user_teams={"it"}, today=TODAY) is True


def test_rule_visibility_rejects_invalid_rule() -> None:
    """规则不可解析时不能按「没配置黑名单」放行。"""
    rule = resolve_effective_rule("{not-json")
    assert is_rule_visible(rule, user_teams={"it"}, today=TODAY) is False


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


def test_org_member_with_org_carries_user_profile() -> None:
    """组织成员联表必须带出用户档案，页面才能以人为主体展示。"""
    row = SbpSysOrgMemberWithOrgOutSchema.model_validate(
        {
            "org_id": 1,
            "org_status": "1",
            "org_deleted": 0,
            "user_id": 42,
            "user_username": "zhangsan",
            "user_cn_name": "张三",
            "user_position_name": "解决方案架构师",
            "user_rank_id": 3,
            "user_rank_name": "P6",
            "user_status": "1",
            "user_deleted": 0,
        }
    )
    assert (row.user_username, row.user_cn_name, row.user_rank_name) == (
        "zhangsan",
        "张三",
        "P6",
    )
    assert (row.user_exists, row.user_valid, row.org_valid) == (True, True, True)


def test_sys_user_model_excludes_sensitive_columns() -> None:
    """用户档案只映射可展示列；敏感列不在模型上就不会被查询带出。"""
    columns = set(SbpSysUserModel.__table__.columns.keys())
    assert columns == {
        "id",
        "username",
        "cn_name",
        "en_name",
        "rank_id",
        "position_name",
        "status",
        "allow_login",
        "deleted",
    }
    assert not columns & {"password_hash", "email", "phone"}


def test_compose_org_path_stops_at_cycle_and_missing_parent() -> None:
    """层级路径向上拼接时，悬挂 parent 与互指成环都不能死循环。"""
    names = {1: ("总部", 0), 2: ("研发中心", 1), 3: ("平台组", 2)}
    assert (
        SbpSysOrgMemberService._compose_path(3, names) == "总部 / 研发中心 / 平台组"
    )
    # 悬挂 parent：只拼到能解析的部分
    assert SbpSysOrgMemberService._compose_path(9, {9: ("孤儿部门", 999)}) == "孤儿部门"
    # A↔B 环：在每个节点只出现一次的前提下截断
    assert SbpSysOrgMemberService._compose_path(5, {5: ("A", 6), 6: ("B", 5)}) == "B / A"


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


# ── 子树展开（含下级下钻） ────────────────────────────────────────


def test_collect_subtree_ids_walks_children_and_survives_cycles() -> None:
    """「含下级」展开：子孙全收，自引用与互指成环都不能死循环。"""
    edges = [
        (1, 0),
        (2, 1),
        (3, 2),
        (4, 999),  # 悬挂父节点，不属于任何已选子树
        (5, 5),  # 自引用
        (6, 8),
        (8, 6),  # 与 8 互指成环
    ]
    assert SbpSysOrgService._collect_subtree_ids(1, edges) == [1, 2, 3]
    # 环上不会无限展开，且每个节点只出现一次
    assert sorted(SbpSysOrgService._collect_subtree_ids(6, edges)) == [6, 8]
    # 根不在表里：返回自身，调用方得到「空结果」而不是「没有过滤条件」
    assert SbpSysOrgService._collect_subtree_ids(99, edges) == [99]


def test_user_query_param_keeps_relation_filters_out_of_column_conditions() -> None:
    """组织/团队过滤是关系条件，不能带 ``q`` 标记。

    带上 ``q`` 会被 ``search_to_dict`` 包成 ``(op, val)`` 元组，service 里
    pop 出来的就不是 ID 而是元组；不带标记则必须由 service 主动 pop，
    否则会被 ``_build_conditions`` 当未知字段静默忽略，变成「加了条件却全量返回」。
    """
    fields = SbpSysUserQueryParam.model_fields
    assert fields["include_sub"].default is False
    for name in ("org_id", "include_sub", "team_id"):
        assert (fields[name].json_schema_extra or {}).get("q") is None
