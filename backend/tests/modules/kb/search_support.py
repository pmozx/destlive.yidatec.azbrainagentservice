"""#07 检索回归的公共装置：库准备、授权链造数、签名请求。

关键词与自然语言两条通道共用同一套「授权 + 生效指针 + 状态」的摆法，装置集中在这里；
两个测试模块只关心各自的召回语义（字面命中 / 语义命中）。

数据直接落到三张表 + GSI 授权链上，而不走 #03 写入链路：
要验的是「检索看到什么」，每个过滤条件都必须能被单独摆出来；
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
from app.modules.kb.revision.model import KbRevisionModel
from app.modules_gsi.km.doc_publication.model import SbpKmDocPublicationModel
from app.modules_gsi.sys.audience_team.model import SbpSysAudienceTeamModel
from app.modules_gsi.sys.audience_team_member.model import SbpSysAudienceTeamMemberModel

#: 测试专用用户号段：避开真实账号，清理时只需按 team_id 回收
USER_ID = 900_001
TENANT = "gsi-default"
TITLE = "VPN申请操作手册"
KNOWLEDGE_NO = "KB-0000064"
SEARCH_PATH = "/v1/knowledge/search"
CONTENT_HASH = "0" * 64

#: GSI 共享表由 GSI 侧 Flyway 维护，AI 侧只读；缺表时整个模块跳过而不是红一片
GSI_TABLES = (
    "sbp_sys_audience_team",
    "sbp_sys_audience_team_member",
    "sbp_km_doc_publication",
)


_SCHEMA_READY = False


async def ready() -> None:
    """首次调用时补齐 AI 域表、确认 GSI 共享表在位；缺 GSI 表时跳过本用例。

    单独做成函数而不是 autouse fixture：切词/打分/摘要等纯函数用例不该被库绑定，
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
            for name in GSI_TABLES
            if (await session.execute(text(f"select to_regclass('public.{name}')"))).scalar() is None
        ]
    if missing:
        pytest.skip(f"GSI 共享表缺失，跳过检索授权用例：{', '.join(missing)}")
    _SCHEMA_READY = True


# ── 数据装置 ─────────────────────────────────────────────────────────


async def team(*, members: list[int], status: str = "1", deleted: int = 0) -> tuple[int, str]:
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


async def gsi_publication(*, publication_id: str, team: str, record_id: str) -> None:
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


async def publication(
    publication_id: str,
    *,
    current_record_id: str,
    tenant_id: str = TENANT,
    current_revision: int = 101,
    status: str = "active",
) -> None:
    """发布单元主表：``current_record_id`` 是检索唯一认可的生效指针。"""
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


async def snapshot(
    publication_id: str,
    record_id: str,
    chunks: list[dict[str, Any]],
    *,
    revision: int = 101,
    searchable: bool = True,
    chunk_status: str = "online",
) -> None:
    """一个版本快照：revision 行 + 若干切片。

    切片字典可带 ``chunk_id`` / ``heading`` / ``page_from`` / ``page_to`` / ``embedding``；
    ``chunk_id`` 缺省按 ``record_id:seq``，向量缺省为 NULL（关键词用例不需要向量）。
    """
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
                embedding=chunk.get("embedding"),
                status=chunk_status,
            )
            for chunk in chunks
        )


async def entry(
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
    await gsi_publication(publication_id=publication_id, team=team, record_id=snapshot_id)
    await publication(
        publication_id,
        current_record_id=snapshot_id,
        tenant_id=tenant_id,
        current_revision=revision,
        status=publication_status,
    )
    await snapshot(
        publication_id,
        snapshot_id,
        chunks,
        revision=revision,
        searchable=searchable,
        chunk_status=chunk_status,
    )
    return publication_id, snapshot_id


async def cleanup(publication_ids: list[str], team_ids: list[int]) -> None:
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
                delete(SbpSysAudienceTeamMemberModel).where(SbpSysAudienceTeamMemberModel.team_id.in_(team_ids))
            )
            await session.execute(delete(SbpSysAudienceTeamModel).where(SbpSysAudienceTeamModel.id.in_(team_ids)))


# ── 请求装置 ─────────────────────────────────────────────────────────


def search_payload(
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


async def search(payload: dict[str, Any]):
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
