"""管理端主动治理：检测（#17）/ 保存前查重（#16）/ 合并预览（#19）。

管理端没有 GSI 签发 ``scope``，范围改由 ``publication_ids`` 表达、后端按当前生效快照反查。
所以这里同时验两条线：

1. **范围反查正确**：给定发布单元能算出与本地一致的 ``record_id`` / ``content_hash``，
   否则 ``KbDupSource`` 会一路 409，页面永远拿不到分组；
2. **契约口径一致**：检测写 30 分钟快照并物化分组、查重只报不拦、预览写回 ``merged_preview``。
"""

import hashlib
import json
import uuid
from typing import Any

from sqlalchemy import delete

from app.core.database import async_session_factory
from app.core.table_bootstrap import ensure_pgvector_extension
from app.modules.kb.bootstrap import ensure_tables
from app.modules.kb.chunk.model import KbChunkModel
from app.modules.kb.dup_group.model import KbDupGroupModel
from app.modules.kb.dup_result.model import KbDupResultModel
from app.modules.kb.publication.model import KbPublicationModel
from app.modules.kb.revision.model import KbRevisionModel

from tests.support import call, requires_db

CONTENT = "VPN 申请需要先在服务台提交工单，由部门负责人审核后由 IT 开通账号。"
TITLE = "VPN 申请与开通说明"


async def _prepare_tables() -> None:
    await ensure_pgvector_extension()
    await ensure_tables()


async def _seed_publication(*, publication_id: str, tenant_id: str) -> None:
    """造一条「已生效」的发布单元：指针、版本摘要与切片必须彼此自洽。"""
    record_id = f"record-{publication_id}"
    content_hash = hashlib.sha256(CONTENT.encode()).hexdigest()
    async with async_session_factory() as session, session.begin():
        session.add(
            KbPublicationModel(
                publication_id=publication_id,
                resource_type="document",
                tenant_id=tenant_id,
                resource_id=f"res-{publication_id}",
                title=TITLE,
                status="active",
                current_record_id=record_id,
                current_revision=1,
            )
        )
        session.add(
            KbRevisionModel(
                publication_id=publication_id,
                record_id=record_id,
                revision=1,
                status="online",
                chunk_count=1,
                content_hash=content_hash,
            )
        )
        session.add(
            KbChunkModel(
                publication_id=publication_id,
                record_id=record_id,
                revision=1,
                seq=1,
                content=CONTENT,
                content_hash=content_hash,
                status="online",
            )
        )


async def _cleanup(*, tenant_id: str, publication_ids: list[str]) -> None:
    """按租户清治理台账，按发布单元清内容（表间无外键，顺序自上而下）。"""
    async with async_session_factory() as session, session.begin():
        await session.execute(delete(KbDupGroupModel).where(KbDupGroupModel.tenant_id == tenant_id))
        await session.execute(delete(KbDupResultModel).where(KbDupResultModel.tenant_id == tenant_id))
        for model in (KbChunkModel, KbRevisionModel, KbPublicationModel):
            await session.execute(delete(model).where(model.publication_id.in_(publication_ids)))


async def _detect(body: dict) -> Any:
    return await call("POST", "/api/v1/kb/dup-groups/detect", body=json.dumps(body).encode())


@requires_db
async def test_detect_check_preview_roundtrip() -> None:
    """检测 → 列表 → 查重 → 合并预览：一条完整的管理端治理链路。"""
    await _prepare_tables()
    tenant_id = f"tenant-{uuid.uuid4().hex[:8]}"
    publication_ids = [f"pub-a-{uuid.uuid4().hex[:6]}", f"pub-b-{uuid.uuid4().hex[:6]}"]
    for publication_id in publication_ids:
        await _seed_publication(publication_id=publication_id, tenant_id=tenant_id)
    try:
        # ── 检测：同内容的两个发布单元应产出 doc_exact 分组 ────────────
        detected = await _detect(
            {
                "tenant_id": tenant_id,
                "publication_ids": publication_ids,
                "kinds": ["doc_exact", "doc_similar"],
            }
        )
        assert detected.status_code == 200
        outcome = detected.json()["data"]
        assert outcome["total_groups"] >= 1
        assert outcome["scope_size"] == 2
        assert outcome["result_id"]
        group = outcome["items"][0]
        assert group["kind"] == "doc_exact"
        assert group["group_id"]
        assert [item["hit_id"] for item in group["items"]]

        # ── 列表：检测结果已物化成治理行（否则页面刷新看不到） ──────────
        listed = await call("GET", f"/api/v1/kb/dup-groups/list?tenant_id={tenant_id}")
        assert listed.status_code == 200
        rows = listed.json()["data"]["items"]
        assert rows and rows[0]["group_key"] == group["group_id"]
        group_pk = rows[0]["id"]

        # ── 查重：候选内容与范围内容一致，应命中且相似度为 1 ────────────
        checked = await call(
            "POST",
            "/api/v1/kb/dup-groups/check",
            body=json.dumps(
                {
                    "tenant_id": tenant_id,
                    "publication_ids": publication_ids,
                    "kinds": ["doc_exact", "doc_similar"],
                    "candidate": {
                        "resource_type": "document",
                        "resource_id": "res-draft",
                        "title": TITLE,
                        "content": CONTENT,
                    },
                }
            ).encode(),
        )
        assert checked.status_code == 200
        check_data = checked.json()["data"]
        assert check_data["is_duplicate"] is True
        assert check_data["similarity"] >= 0.65
        assert check_data["matches"]

        # ── 预览：契约要求每个成员恰好一个动作，主项必须是 keep ─────────
        detail = await call("GET", f"/api/v1/kb/dup-groups/detail/{group_pk}")
        assert detail.status_code == 200
        members = detail.json()["data"]["members"]
        keep_hit_id = members[0]["hit_id"]
        actions = [
            {"target_hit_id": member["hit_id"], "action": "keep" if member["hit_id"] == keep_hit_id else "drop"}
            for member in members
        ]
        previewed = await call(
            "POST",
            f"/api/v1/kb/dup-groups/preview/{group_pk}",
            body=json.dumps({"keep_hit_id": keep_hit_id, "actions": actions}).encode(),
        )
        assert previewed.status_code == 200
        preview = previewed.json()["data"]
        assert preview["preview_hash"].startswith("sha256:")
        assert preview["title"] == TITLE
        assert keep_hit_id in preview["source_hit_ids"]

        # 预览是草稿态但会写回分组行，页面刷新后才看得到「已生成」
        refreshed = await call("GET", f"/api/v1/kb/dup-groups/detail/{group_pk}")
        merged_preview = refreshed.json()["data"]["merged_preview"]
        assert merged_preview is not None
        assert merged_preview["preview_hash"] == preview["preview_hash"]
        assert merged_preview["keep_hit_id"] == keep_hit_id
    finally:
        await _cleanup(tenant_id=tenant_id, publication_ids=publication_ids)


@requires_db
async def test_detect_rejects_unknown_publication() -> None:
    """范围里有不存在的发布单元直接 400：静默少比等于报假成功。"""
    await _prepare_tables()
    tenant_id = f"tenant-{uuid.uuid4().hex[:8]}"
    publication_id = f"pub-c-{uuid.uuid4().hex[:6]}"
    await _seed_publication(publication_id=publication_id, tenant_id=tenant_id)
    try:
        response = await _detect(
            {
                "tenant_id": tenant_id,
                "publication_ids": [publication_id, "pub-not-exist"],
                "kinds": ["doc_exact"],
            }
        )
        assert response.status_code == 400
        assert "不存在" in response.json()["msg"]
    finally:
        await _cleanup(tenant_id=tenant_id, publication_ids=[publication_id])


@requires_db
async def test_detect_rejects_empty_scope() -> None:
    """范围内没有生效内容时 400，不返回「零分组」冒充检测通过。"""
    await _prepare_tables()
    response = await _detect({"tenant_id": f"tenant-{uuid.uuid4().hex[:8]}", "kinds": ["doc_exact"]})
    assert response.status_code == 400
    assert "没有生效" in response.json()["msg"]


@requires_db
async def test_preview_without_snapshot_returns_conflict() -> None:
    """手工造的分组没有检测快照：预览 409，提示先重新发起检测。"""
    await _prepare_tables()
    tenant_id = f"tenant-{uuid.uuid4().hex[:8]}"
    group_key = f"manual-{uuid.uuid4().hex[:8]}"
    async with async_session_factory() as session, session.begin():
        session.add(
            KbDupGroupModel(
                tenant_id=tenant_id,
                group_key=group_key,
                kind="doc_exact",
                title=TITLE,
                resource_type="document",
                members=[],
                status="active",
            )
        )
    try:
        listed = await call("GET", f"/api/v1/kb/dup-groups/list?tenant_id={tenant_id}")
        row_id = listed.json()["data"]["items"][0]["id"]
        response = await call(
            "POST",
            f"/api/v1/kb/dup-groups/preview/{row_id}",
            body=json.dumps({"keep_hit_id": "hit-1", "actions": []}).encode(),
        )
        assert response.status_code == 409
        assert "检测范围" in response.json()["msg"]
    finally:
        await _cleanup(tenant_id=tenant_id, publication_ids=[])
