"""#07 关键词检索回归（T4.1）：命中 / 授权下推 / 生效指针 / 参数校验。

验收断言：
1. keyword 命中返回 chunk 级 hits，字段与契约 #07 对齐、`score` 落在 0–1；
2. 多个关键词按 **AND** 命中（每个词都必须出现在正文或标题里）；
3. 授权按受众团队下推：非成员团队的发布单元即使命中也是 0 条（不是「召回后再过滤」）；
4. 租户、``searchable``、发布单元状态、当前生效指针都是硬过滤条件；
5. 无受众团队 / ``user_id`` 非数字 → 空结果 ``no_evidence=true``，不报 500；
6. 参数非法 → 400 ``INVALID_REQUEST``；``mode=natural_language`` 的语义检索见
   ``test_natural_language_search.py``（本文件只管关键词通道）。

数据装置在 ``search_support.py``（直接落到三张表 + GSI 授权链上，而不走 #03 写入链路）：
本模块要验的是「检索看到什么」，每个过滤条件都必须能被单独摆出来；
写入链路的正确性由 ``test_entry_write.py`` 负责。
"""

import uuid
from typing import Any

from app.modules.kb.retrieval.service import _score, _tokenize
from app.modules_api.base import errors as contract_errors

from tests.support import requires_db

# 数据装置与签名请求集中在 ``search_support``：两个检索模块（关键词 / 自然语言）共用同一套
# 摆法，这里用别名保留既有用例的短名。
from tests.modules.kb.search_support import (
    KNOWLEDGE_NO,
    TITLE,
    USER_ID,
    cleanup as _cleanup,
    entry as _entry,
    gsi_publication as _gsi_publication,
    publication as _publication,
    ready as _ready,
    search as _search,
    search_payload as _search_payload,
    snapshot as _snapshot,
    team as _team,
)


def _vpn_chunks() -> list[dict[str, Any]]:
    """两条切片：只有第 1 条同时含「vpn」与「申请」，第 2 条只含「申请 / 审核」。"""
    return [
        {
            "seq": 1,
            "heading": "申请流程",
            "content": "在服务台提交 vpn 申请，由负责人开通账号。",
            "page_from": 1,
            "page_to": 1,
        },
        {
            "seq": 2,
            "heading": "审批时限",
            "content": "普通申请 1 个工作日内完成审核，涉及生产网段需额外复核。",
            "page_from": 1,
            "page_to": 2,
        },
    ]


# ── 纯函数：切词 / 打分 ───────────────────────────────────────


def test_tokenize_splits_on_punctuation_and_dedupes() -> None:
    """中文按标点整段切、去重保序：重复词只留一份，全角问号不当字符。"""
    assert _tokenize("  如何申请 VPN？VPN  ") == ("如何申请", "VPN")
    assert _tokenize("，、 。") == ()
    assert len(_tokenize(" ".join(f"w{index}" for index in range(20)))) == 8


def test_score_rewards_coverage_density_and_heading() -> None:
    """覆盖率 / 密度 / 标题命中都拉满 → 1.0；完全不命中 → 0；少命中一个词必然掉分。"""
    assert _score("vpn vpn vpn 申请 申请 申请", "vpn 申请", ("vpn", "申请")) == 1.0
    assert _score("无关正文", None, ("vpn",)) == 0.0
    assert _score("vpn 申请", None, ("vpn", "申请")) > _score("vpn", None, ("vpn", "申请"))


# ── 接口回归 ─────────────────────────────────────────────────────────


@requires_db
async def test_keyword_search_returns_chunk_hit_with_contract_fields() -> None:
    """命中：chunk 级 hits，字段与契约一致，`score` 落在 0–1、`snippet` 是本切片的完整正文。"""
    await _ready()
    team_id, group_code = await _team(members=[USER_ID])
    publication_id, record_id = await _entry(team=group_code, chunks=_vpn_chunks())
    try:
        response = await _search(_search_payload(query="vpn"))
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["returned_count"] == len(body["hits"]) == 1
        assert body["no_evidence"] is False

        hit = body["hits"][0]
        assert hit["publication_id"] == publication_id
        assert hit["record_id"] == record_id
        assert hit["chunk_id"] == f"{record_id}:1"
        assert hit["resource_type"] == "document"
        assert hit["resource_id"] == f"doc-{publication_id}"
        assert (hit["version_id"], hit["revision"], hit["knowledge_no"]) == ("1", 101, KNOWLEDGE_NO)
        assert (hit["title"], hit["page_from"], hit["page_to"]) == (TITLE, 1, 1)
        assert 0.0 < hit["score"] <= 1.0
        # 契约 #04 的示例值就是切片全文：不截断、不折叠、不补省略号
        assert hit["snippet"] == "在服务台提交 vpn 申请，由负责人开通账号。"
    finally:
        await _cleanup([publication_id], [team_id])


@requires_db
async def test_keyword_search_matches_every_keyword() -> None:
    """多词按 AND：`vpn 审核` 无切片同时命中（0 条），`vpn 申请` 只命中第 1 条。"""
    await _ready()
    team_id, group_code = await _team(members=[USER_ID])
    publication_id, record_id = await _entry(team=group_code, chunks=_vpn_chunks())
    try:
        both = await _search(_search_payload(query="vpn 申请"))
        assert both.status_code == 200, both.text
        assert [hit["chunk_id"] for hit in both.json()["hits"]] == [f"{record_id}:1"]

        missing = await _search(_search_payload(query="vpn 审核"))
        assert missing.status_code == 200, missing.text
        assert missing.json()["hits"] == []
        assert missing.json()["returned_count"] == 0
        assert missing.json()["no_evidence"] is True
    finally:
        await _cleanup([publication_id], [team_id])


@requires_db
async def test_keyword_search_is_scoped_to_audience_team() -> None:
    """授权下推：同关键词、同租户，非成员团队（团队 B）的发布单元一条都不返回。"""
    await _ready()
    team_a_id, team_a = await _team(members=[USER_ID])
    team_b_id, team_b = await _team(members=[])
    mine_id, _ = await _entry(team=team_a, chunks=[{"seq": 1, "content": "vpn 授权内正文"}])
    other_id, _ = await _entry(team=team_b, chunks=[{"seq": 1, "content": "vpn 越权正文"}])
    try:
        response = await _search(_search_payload(query="vpn"))
        assert response.status_code == 200, response.text
        body = response.json()
        assert [hit["publication_id"] for hit in body["hits"]] == [mine_id]
        assert body["returned_count"] == 1
    finally:
        await _cleanup([mine_id, other_id], [team_a_id, team_b_id])


@requires_db
async def test_keyword_search_is_scoped_to_tenant() -> None:
    """租户是硬过滤：同一授权团队下，其它租户的发布单元不返回。"""
    await _ready()
    team_id, group_code = await _team(members=[USER_ID])
    mine_id, _ = await _entry(team=group_code, chunks=[{"seq": 1, "content": "vpn 本租户正文"}])
    other_id, _ = await _entry(
        team=group_code, chunks=[{"seq": 1, "content": "vpn 他租户正文"}], tenant_id="tenant-b"
    )
    try:
        response = await _search(_search_payload(query="vpn"))
        assert response.status_code == 200, response.text
        assert [hit["publication_id"] for hit in response.json()["hits"]] == [mine_id]
    finally:
        await _cleanup([mine_id, other_id], [team_id])


@requires_db
async def test_keyword_search_returns_no_evidence_without_team() -> None:
    """用户不在任何受众团队：授权集合为空 → 空结果 `no_evidence=true`（不是 403、也不是全库）。"""
    await _ready()
    team_id, group_code = await _team(members=[])
    publication_id, _ = await _entry(team=group_code, chunks=[{"seq": 1, "content": "vpn 无人可见正文"}])
    try:
        response = await _search(_search_payload(query="vpn", user_id="900999"))
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["hits"] == []
        assert body["returned_count"] == 0
        assert body["no_evidence"] is True
    finally:
        await _cleanup([publication_id], [team_id])


@requires_db
async def test_keyword_search_ignores_non_numeric_user_id() -> None:
    """`user_id` 不是数字主键：按无权限处理（空结果），不抛 500、更不放行。"""
    await _ready()
    response = await _search(_search_payload(query="vpn", user_id="gsi-user-abc"))
    assert response.status_code == 200, response.text
    assert response.json()["no_evidence"] is True


@requires_db
async def test_keyword_search_reads_current_snapshot_only() -> None:
    """只召回当前生效快照：旧 `record_id` 的切片仍在库里，但不得出现在结果中。"""
    await _ready()
    team_id, group_code = await _team(members=[USER_ID])
    publication_id = f"pub-{uuid.uuid4().hex[:12]}"
    old_record = f"record-{uuid.uuid4().hex[:12]}"
    new_record = f"record-{uuid.uuid4().hex[:12]}"
    try:
        await _gsi_publication(publication_id=publication_id, team=group_code, record_id=new_record)
        await _publication(publication_id, current_record_id=new_record, current_revision=102)
        await _snapshot(publication_id, old_record, [{"seq": 1, "content": "vpn 历史版本正文"}], revision=101)
        await _snapshot(publication_id, new_record, [{"seq": 1, "content": "vpn 当前版本正文"}], revision=102)

        response = await _search(_search_payload(query="vpn"))
        assert response.status_code == 200, response.text
        hits = response.json()["hits"]
        assert [hit["record_id"] for hit in hits] == [new_record]
        assert hits[0]["revision"] == 102
    finally:
        await _cleanup([publication_id], [team_id])


@requires_db
async def test_keyword_search_skips_unsearchable_or_inactive_publication() -> None:
    """`searchable=false`、发布单元非 active、切片非 online 都不参与检索。"""
    await _ready()
    team_id, group_code = await _team(members=[USER_ID])
    chunks = [{"seq": 1, "content": "vpn 统一命中正文"}]
    unsearchable_id, _ = await _entry(team=group_code, chunks=chunks, searchable=False)
    inactive_id, _ = await _entry(team=group_code, chunks=chunks, publication_status="inactive")
    archived_id, _ = await _entry(team=group_code, chunks=chunks, chunk_status="archived")
    alive_id, _ = await _entry(team=group_code, chunks=chunks)
    try:
        response = await _search(_search_payload(query="vpn"))
        assert response.status_code == 200, response.text
        assert [hit["publication_id"] for hit in response.json()["hits"]] == [alive_id]
    finally:
        await _cleanup([unsearchable_id, inactive_id, archived_id, alive_id], [team_id])


@requires_db
async def test_keyword_search_rejects_invalid_payload() -> None:
    """契约参数非法一律 400 `INVALID_REQUEST`（FastAPI 的 422 已归一）。"""
    await _ready()
    base = _search_payload(query="vpn")
    missing_auth = _search_payload(query="vpn")
    missing_auth.pop("authorization")
    cases = {
        "mode 非法": {**base, "mode": "vector"},
        "limit 下界": {**base, "limit": 0},
        "limit 上界": {**base, "limit": 101},
        "query 空白": {**base, "query": "   "},
        "缺 authorization": missing_auth,
    }

    for name, payload in cases.items():
        response = await _search(payload)
        assert response.status_code == 400, f"{name}: {response.text}"
        assert response.json()["error"]["code"] == contract_errors.INVALID_REQUEST, name
