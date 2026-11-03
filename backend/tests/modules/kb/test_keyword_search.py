"""#07 关键词检索回归（T4.1）：命中 / 授权下推 / 生效指针 / 参数校验。

验收断言：
1. keyword 命中返回 chunk 级 hits，字段与契约 #07 对齐、`score` 落在 0–1；
2. 多个关键词按 **AND** 命中（每个词都必须出现在正文或标题里）；
3. 授权按受众团队下推：非成员团队的发布单元即使命中也是 0 条（不是「召回后再过滤」）；
4. 租户、``searchable``、发布单元状态、当前生效指针都是硬过滤条件；
5. 无受众团队 / ``user_id`` 非数字 → 空结果 ``no_evidence=true``，不报 500；
6. 参数非法 → 400 ``INVALID_REQUEST``；``mode=natural_language`` → 501（T4.2 交付）。

数据直接落到三张表 + GSI 授权链上，而不走 #03 写入链路：
本模块要验的是「检索看到什么」，每个过滤条件都必须能被单独摆出来；
写入链路的正确性由 ``test_entry_write.py`` 负责。
"""

import json
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, text

from app import create_app
from app.config.setting import settings
from app.core.database import async_session_factory
from app.core.security_signature import build_signed_headers
from app.core.table_bootstrap import ensure_pgvector_extension
from app.modules.kb.bootstrap import ensure_tables
from app.modules.kb.chunk.model import KbChunkModel
from app.modules.kb.publication.model import KbPublicationModel
from app.modules.kb.retrieval.service import _score, _snippet, _tokenize
from app.modules.kb.revision.model import KbRevisionModel
from app.modules_gsi.km.doc_publication.model import SbpKmDocPublicationModel
from app.modules_gsi.sys.audience_team.model import SbpSysAudienceTeamModel
from app.modules_gsi.sys.audience_team_member.model import SbpSysAudienceTeamMemberModel
from app.modules_api.contract import errors as contract_errors

from tests.support import requires_db

#: 测试专用用户号段：避开真实账号，清理时只需按 team_id 回收
USER_ID = 900_001
TENANT = "gsi-default"
TITLE = "VPN申请操作手册"
KNOWLEDGE_NO = "KB-0000064"
SEARCH_PATH = "/v1/knowledge/search"
CONTENT_HASH = "0" * 64

#: GSI 共享表由 GSI 侧 Flyway 维护，AI 侧只读；缺表时整个模块跳过而不是红一片
_GSI_TABLES = (
    "sbp_sys_audience_team",
    "sbp_sys_audience_team_member",
    "sbp_km_doc_publication",
)


_SCHEMA_READY = False


async def _ready() -> None:
    """首次调用时补齐 AI 域表、确认 GSI 共享表在位；缺 GSI 表时跳过本用例。

    单独做成函数而不是 autouse fixture：切词/打分/摘要三条纯函数用例不该被库绑定，
    本地无库时它们仍应能跑。
    """
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    await ensure_pgvector_extension()
    await ensure_tables()
    async with async_session_factory() as session:
        missing = [
            name
            for name in _GSI_TABLES
            if (await session.execute(text(f"select to_regclass('public.{name}')"))).scalar() is None
        ]
    if missing:
        pytest.skip(f"GSI 共享表缺失，跳过检索授权用例：{', '.join(missing)}")
    _SCHEMA_READY = True


# ── 数据装置 ─────────────────────────────────────────────────────────


async def _team(*, members: list[int], status: str = "1", deleted: int = 0) -> tuple[int, str]:
    """建一个受众团队（含成员），返回 ``(team_id, group_code)``。"""
    team_id = int(uuid.uuid4().hex[:8], 16) + 1_000_000
    group_code = f"kb-team-{team_id}"
    async with async_session_factory() as session, session.begin():
        session.add(
            SbpSysAudienceTeamModel(
                id=team_id,
                group_code=group_code,
                group_name=f"测试团队 {team_id}",
                status=status,
                deleted=deleted,
            )
        )
        for user_id in members:
            session.add(SbpSysAudienceTeamMemberModel(team_id=team_id, user_id=user_id))
    return team_id, group_code


async def _gsi_publication(*, publication_id: str, team: str, record_id: str) -> None:
    """GSI 侧「文档按团队生效」一行：``id`` 即契约 ``publication_id``。"""
    async with async_session_factory() as session, session.begin():
        session.add(
            SbpKmDocPublicationModel(
                id=publication_id,
                doc_id=f"doc-{publication_id}",
                team=team,
                record_id=record_id,
                index_status="succeeded",
                updated_at=datetime.now(UTC).replace(tzinfo=None),
            )
        )


async def _publication(
    publication_id: str,
    *,
    current_record_id: str,
    tenant_id: str = TENANT,
    current_revision: int = 101,
    status: str = "active",
) -> None:
    async with async_session_factory() as session, session.begin():
        session.add(
            KbPublicationModel(
                publication_id=publication_id,
                tenant_id=tenant_id,
                resource_id=f"doc-{publication_id}",
                resource_type="document",
                current_record_id=current_record_id,
                current_revision=current_revision,
                status=status,
                title=TITLE,
            )
        )


async def _snapshot(
    publication_id: str,
    record_id: str,
    chunks: list[dict[str, Any]],
    *,
    revision: int = 101,
    searchable: bool = True,
    chunk_status: str = "online",
) -> None:
    """一个版本快照：revision 行 + 若干切片（``chunk_id`` 缺省按 ``record_id:seq``）。"""
    async with async_session_factory() as session, session.begin():
        session.add(
            KbRevisionModel(
                publication_id=publication_id,
                record_id=record_id,
                version_id="1",
                knowledge_no=KNOWLEDGE_NO,
                revision=revision,
                content_hash=CONTENT_HASH,
                chunk_count=len(chunks),
                searchable=searchable,
                status="online",
            )
        )
        session.add_all(
            KbChunkModel(
                publication_id=publication_id,
                record_id=record_id,
                revision=revision,
                chunk_id=chunk.get("chunk_id") or f"{record_id}:{chunk['seq']}",
                seq=chunk["seq"],
                heading=chunk.get("heading"),
                content=chunk["content"],
                page_from=chunk.get("page_from"),
                page_to=chunk.get("page_to"),
                content_hash=CONTENT_HASH,
                status=chunk_status,
            )
            for chunk in chunks
        )


async def _entry(
    *,
    team: str,
    chunks: list[dict[str, Any]],
    record_id: str | None = None,
    tenant_id: str = TENANT,
    revision: int = 101,
    searchable: bool = True,
    publication_status: str = "active",
    chunk_status: str = "online",
) -> tuple[str, str]:
    """一个「已授权 + 当前生效」的发布单元：返回 ``(publication_id, record_id)``。"""
    publication_id = f"pub-{uuid.uuid4().hex[:12]}"
    snapshot_id = record_id or f"record-{uuid.uuid4().hex[:12]}"
    await _gsi_publication(publication_id=publication_id, team=team, record_id=snapshot_id)
    await _publication(
        publication_id,
        current_record_id=snapshot_id,
        tenant_id=tenant_id,
        current_revision=revision,
        status=publication_status,
    )
    await _snapshot(
        publication_id,
        snapshot_id,
        chunks,
        revision=revision,
        searchable=searchable,
        chunk_status=chunk_status,
    )
    return publication_id, snapshot_id


async def _cleanup(publication_ids: list[str], team_ids: list[int]) -> None:
    """按 publication_id / team_id 回收，GSI 与 AI 两侧都不留脏数据。"""
    async with async_session_factory() as session, session.begin():
        for publication_id in publication_ids:
            for model in (KbChunkModel, KbRevisionModel, KbPublicationModel):
                await session.execute(delete(model).where(model.publication_id == publication_id))
            await session.execute(
                delete(SbpKmDocPublicationModel).where(SbpKmDocPublicationModel.id == publication_id)
            )
        if team_ids:
            await session.execute(
                delete(SbpSysAudienceTeamMemberModel).where(
                    SbpSysAudienceTeamMemberModel.team_id.in_(team_ids)
                )
            )
            await session.execute(
                delete(SbpSysAudienceTeamModel).where(SbpSysAudienceTeamModel.id.in_(team_ids))
            )


# ── 请求装置 ─────────────────────────────────────────────────────────


def _search_payload(
    *,
    query: str,
    user_id: str = str(USER_ID),
    tenant_id: str = TENANT,
    mode: str = "keyword",
    limit: int = 5,
) -> dict[str, Any]:
    """#07 正文：``authorization`` 必须在 120s 时效窗口内，否则路由依赖先回 401。"""
    now = int(time.time() * 1000)
    return {
        "request_id": f"req-{uuid.uuid4().hex[:12]}",
        "query": query,
        "mode": mode,
        "limit": limit,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "authorization": {
            "scope_id": f"scope-{uuid.uuid4().hex[:8]}",
            "issued_at": now - 1_000,
            "expires_at": now + 60_000,
        },
    }


async def _search(payload: dict[str, Any]):
    """签发一组新头再调用 #07：每个用例都换 nonce，避免撞上重放拒绝。"""
    body = json.dumps(payload, ensure_ascii=False).encode()
    headers = {
        "Content-Type": "application/json",
        **build_signed_headers(
            settings.CONTRACT_V1_SHARED_SECRET,
            client_id=settings.CONTRACT_V1_CLIENT_ID,
            method="POST",
            path=SEARCH_PATH,
            body=body,
            nonce=f"search-{uuid.uuid4().hex}",
        ),
    }
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
        return await client.request("POST", SEARCH_PATH, content=body, headers=headers)


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


# ── 纯函数：切词 / 打分 / 摘要 ───────────────────────────────────────


def test_tokenize_splits_on_punctuation_and_dedupes() -> None:
    """中文按标点整段切、去重保序：重复词只留一份，全角问号不当字符。"""
    assert _tokenize("  如何申请 VPN？VPN  ") == ("如何申请", "VPN")
    assert _tokenize("，、 。") == ()
    assert len(_tokenize(" ".join(f"w{index}" for index in range(20)))) == 8


def test_snippet_centers_on_hit_and_marks_truncation() -> None:
    """摘要取命中点附近，首尾被截断时加省略号。"""
    content = "前言" * 80 + "这里提到 vpn 开通流程" + "后记" * 80
    snippet = _snippet(content, ("vpn",))
    assert "vpn" in snippet
    assert snippet.startswith("…") and snippet.endswith("…")
    assert len(snippet) <= settings.SEARCH_SNIPPET_LENGTH + 2


def test_score_rewards_coverage_density_and_heading() -> None:
    """覆盖率 / 密度 / 标题命中都拉满 → 1.0；完全不命中 → 0；少命中一个词必然掉分。"""
    assert _score("vpn vpn vpn 申请 申请 申请", "vpn 申请", ("vpn", "申请")) == 1.0
    assert _score("无关正文", None, ("vpn",)) == 0.0
    assert _score("vpn 申请", None, ("vpn", "申请")) > _score("vpn", None, ("vpn", "申请"))


# ── 接口回归 ─────────────────────────────────────────────────────────


@requires_db
async def test_keyword_search_returns_chunk_hit_with_contract_fields() -> None:
    """命中：chunk 级 hits，字段与契约一致，`score` 落在 0–1、`snippet` 来自本切片正文。"""
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
        assert "vpn" in hit["snippet"].lower()
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


@requires_db
async def test_natural_language_mode_reports_not_implemented() -> None:
    """`mode=natural_language` 明确回 501，而不是拿空 `hits` 冒充 `no_evidence`。"""
    await _ready()
    payload = _search_payload(query="如何申请 vpn", mode="natural_language")
    response = await _search(payload)
    assert response.status_code == 501, response.text
    body = response.json()
    assert body["error"]["code"] == contract_errors.INTERNAL_ERROR
    assert body["error"]["retryable"] is False
    assert body["request_id"] == payload["request_id"]
