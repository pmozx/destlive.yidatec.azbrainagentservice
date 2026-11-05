"""管理端自助解析提交（``POST /api/v1/kb/parse-jobs/upload``）与产物切片编辑。

与契约 #01 的差别只在文件来源（multipart 上传 vs ``file_url``），所以这里验三件事：

1. 空文件 / 后缀白名单 / 大小上限都在**受理之前**归口成 400 / 413，不产生任务行；
2. 受理回执带 job_id，后台任务跑完后详情能看到解析产物；
3. ``file_meta`` 记录提交来源与解析选项（``submitted_by=admin``），便于与契约链路区分追溯。

切片编辑（``PUT /update-chunk/{job_id}``）另验两件事：只动 ``seq`` 命中的那一条（打 ``edited`` 标记），
且**不重算** ``content_hash``；seq 不存在与任务不存在都是 404。

写入知识平台（``POST /publish/{job_id}``）是契约 03.1 的管理端等价入口：验「切片原样入库」
（条数、顺序、人工修订都不能被重切分改掉）与「同一产物只能写一次」（二次提交 409）。
"""

import json
import uuid
from typing import Any

from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select

from app import create_app
from app.config.setting import settings
from app.core.database import async_session_factory
from app.core.table_bootstrap import ensure_pgvector_extension
from app.modules.kb.bootstrap import ensure_tables
from app.modules.kb.chunk.model import KbChunkModel
from app.modules.kb.parse_job.model import KbParseJobModel
from app.modules.kb.parse_result.model import KbParseResultModel
from app.modules.kb.publication.crud import KbPublicationCRUD

from tests.support import call, requires_db

UPLOAD_PATH = "/api/v1/kb/parse-jobs/upload"
UPDATE_CHUNK_PATH = "/api/v1/kb/parse-jobs/update-chunk/{job_id}"
PUBLISH_PATH = "/api/v1/kb/parse-jobs/publish/{job_id}"


async def _prepare_tables() -> None:
    """补齐 kb 域表（与契约链路共用同一批表）。"""
    await ensure_pgvector_extension()
    await ensure_tables()


async def _upload(*, file_name: str, content: bytes, **form: str):
    """multipart 调用：ASGITransport 不走网络，后台任务在响应后由同一事件循环执行完。"""
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
        return await client.post(UPLOAD_PATH, files={"file": (file_name, content)}, data=form)


async def _cleanup(job_id: str) -> None:
    """清掉任务行与产物（管理端没有删除入口，只能按 job_id 直删）。"""
    async with async_session_factory() as session, session.begin():
        await session.execute(delete(KbParseResultModel).where(KbParseResultModel.job_id == job_id))
        await session.execute(delete(KbParseJobModel).where(KbParseJobModel.uuid == job_id))


async def _count_jobs() -> int:
    """当前任务行总数：用来断言被拒的提交没有留下半成品。"""
    async with async_session_factory() as session:
        return int((await session.execute(select(func.count()).select_from(KbParseJobModel))).scalar() or 0)


@requires_db
async def test_admin_upload_reaches_succeeded() -> None:
    """上传 md → 回执 → 后台解析完成：详情能读到解析产物与提交快照。"""
    await _prepare_tables()
    response = await _upload(
        file_name="vpn-guide.md",
        content="# VPN 申请\n\n在服务台提交申请，负责人审核后开通。\n\n## 排障\n\n先检查客户端版本。".encode(),
        resource_type="document",
        chunk="true",
        language="zh-CN",
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    created: dict[str, Any] = body["data"]
    job_id = created["uuid"]
    try:
        assert created["resource_type"] == "document"
        assert created["status"] in {"queued", "running", "succeeded"}

        detail = await call("GET", f"/api/v1/kb/parse-jobs/detail/{job_id}")
        assert detail.status_code == 200
        payload = detail.json()["data"]
        assert payload["status"] == "succeeded"
        assert payload["progress"] == 100
        # 管理端提交与契约 #01 用同一套 file_meta 口径，靠 submitted_by 区分来源
        assert payload["file_meta"]["submitted_by"] == "admin"
        assert payload["file_meta"]["file_name"] == "vpn-guide.md"
        assert payload["file_meta"]["file_size"] == len(
            "# VPN 申请\n\n在服务台提交申请，负责人审核后开通。\n\n## 排障\n\n先检查客户端版本。".encode()
        )
        assert payload["file_meta"]["options"] == {"chunk": True, "language": "zh-CN"}
        assert payload["result"]["chunks"]
    finally:
        await _cleanup(job_id)


@requires_db
async def test_admin_upload_rejects_before_creating_job() -> None:
    """空文件与不支持的后缀都在建任务之前拒绝，列表里不留半成品任务行。"""
    await _prepare_tables()
    before = await _count_jobs()

    empty = await _upload(file_name="empty.md", content=b"")
    assert empty.status_code == 400
    assert "为空" in empty.json()["msg"]

    suffix = await _upload(file_name="setup.exe", content=b"MZ")
    assert suffix.status_code == 400
    assert ".exe" in suffix.json()["msg"]

    assert await _count_jobs() == before


@requires_db
async def test_admin_upload_rejects_oversized_file(monkeypatch) -> None:
    """超过 ``PARSE_MAX_FILE_BYTES`` 立刻 413（读的时候就中断，不是读完再判）。"""
    await _prepare_tables()
    monkeypatch.setattr(settings, "PARSE_MAX_FILE_BYTES", 16)
    response = await _upload(file_name="big.md", content=b"# title\n\n" + b"x" * 64)
    assert response.status_code == 413
    assert "上限" in response.json()["msg"]


@requires_db
async def test_admin_upload_generates_unique_job_ids() -> None:
    """同文件重复上传是两个任务：管理端没有契约侧的 job_id 幂等键。"""
    await _prepare_tables()
    content = "# 同一份文件\n\n内容一致，但每次提交都应产生新任务。".encode()
    first = await _upload(file_name="same.md", content=content)
    second = await _upload(file_name="same.md", content=content)
    assert first.status_code == 200
    assert second.status_code == 200
    first_id = first.json()["data"]["uuid"]
    second_id = second.json()["data"]["uuid"]
    try:
        assert first_id != second_id
        assert uuid.UUID(first_id).version == 4
    finally:
        await _cleanup(first_id)
        await _cleanup(second_id)


async def _put_chunk(job_id: str, payload: dict[str, Any]):
    """``call`` 只收原始字节，这里统一序列化（与线上发出的字节口径一致）。"""
    return await call("PUT", UPDATE_CHUNK_PATH.format(job_id=job_id), body=json.dumps(payload).encode())


@requires_db
async def test_admin_update_chunk_marks_only_target() -> None:
    """编辑一条切片：只该条变化并打 ``edited``，其余切片不动、产物哈希保持原值。"""
    await _prepare_tables()
    # 每段都要超过 PARSE_CHUNK_SIZE（800 字符）：短段会被合成一个切片，验不出「只动一条」
    paragraph = "本段用于验证切片编辑只影响目标切片，其余切片保持原样。" * 30
    response = await _upload(
        file_name="edit-me.md",
        content=f"# 标题\n\n{paragraph}\n\n## 小节\n\n{paragraph}\n".encode(),
    )
    job_id = response.json()["data"]["uuid"]
    try:
        detail = (await call("GET", f"/api/v1/kb/parse-jobs/detail/{job_id}")).json()["data"]
        chunks = detail["result"]["chunks"]
        assert len(chunks) >= 2, "用例依赖多切片产物，才能验证「只动一条」"
        target = chunks[0]
        original_hash = detail["result"]["content_hash"]

        edited = await _put_chunk(job_id, {"seq": target["seq"], "content": "## 人工修正\n\n这段正文被运维改过。"})
        assert edited.status_code == 200
        out = edited.json()["data"]
        assert out["seq"] == target["seq"]
        assert out["edited"] is True
        assert out["content"] == "## 人工修正\n\n这段正文被运维改过。"
        assert out["edited_time"]

        after = (await call("GET", f"/api/v1/kb/parse-jobs/detail/{job_id}")).json()["data"]
        # 哈希代表整篇解析文本：切片改过也不重算，避免出现「哈希与内容都变了却无从追溯」
        assert after["result"]["content_hash"] == original_hash
        hit = next(item for item in after["result"]["chunks"] if item["seq"] == target["seq"])
        assert hit["content"] == "## 人工修正\n\n这段正文被运维改过。"
        assert hit["edited"] is True
        others = [item for item in after["result"]["chunks"] if item["seq"] != target["seq"]]
        assert all("edited" not in item for item in others)
    finally:
        await _cleanup(job_id)


@requires_db
async def test_admin_update_chunk_rejects_unknown_target() -> None:
    """seq 不存在与任务不存在都回 404：不把「改错了对象」当成静默成功。"""
    await _prepare_tables()
    response = await _upload(file_name="missing-seq.md", content="# 标题\n\n只有一段正文。".encode())
    job_id = response.json()["data"]["uuid"]
    try:
        bad_seq = await _put_chunk(job_id, {"seq": 999, "content": "无处可去的正文"})
        assert bad_seq.status_code == 404
        assert "999" in bad_seq.json()["msg"]

        ghost = await _put_chunk("not-exists-job", {"seq": 1, "content": "任务不存在"})
        assert ghost.status_code == 404
    finally:
        await _cleanup(job_id)


async def _publish(job_id: str, payload: dict[str, Any]):
    """发布请求体很小，同样统一序列化后发原始字节。"""
    return await call("POST", PUBLISH_PATH.format(job_id=job_id), body=json.dumps(payload).encode())


async def _drop_publication(publication_id: str) -> None:
    """连发布单元带快照/切片一起物理删（测试只关心本次写入的结果）。"""
    async with async_session_factory() as session, session.begin():
        await KbPublicationCRUD(session).delete_cascade(publication_id=publication_id)


@requires_db
async def test_admin_publish_writes_approved_chunks_verbatim() -> None:
    """产物写入知识平台：切片原样入库、回执回填产物行，二次提交 409。"""
    await _prepare_tables()
    # 每段超过 PARSE_CHUNK_SIZE：否则产物只有一条切片，验不出「不被重新切分/合并」
    paragraph = "本段用于验证发布链路按产物切片原样入库，不做二次切分。" * 30
    response = await _upload(
        file_name="publish-me.md",
        content=f"# 标题\n\n{paragraph}\n\n## 小节\n\n{paragraph}\n".encode(),
    )
    job_id = response.json()["data"]["uuid"]
    publication_id: str | None = None
    try:
        detail = (await call("GET", f"/api/v1/kb/parse-jobs/detail/{job_id}")).json()["data"]
        chunks = detail["result"]["chunks"]
        assert len(chunks) >= 2, "用例依赖多切片产物，才能验证切片边界没被重切修正"
        target = chunks[0]
        edited_text = "## 人工修正\n\n这段正文在写入前被运维改过。"
        assert (await _put_chunk(job_id, {"seq": target["seq"], "content": edited_text})).status_code == 200

        published = await _publish(job_id, {"title": "发布链路用例文档", "category": "运维"})
        assert published.status_code == 200
        receipt = published.json()["data"]
        publication_id = receipt["publication_id"]
        assert receipt["job_id"] == job_id
        assert receipt["revision"] == 1
        assert receipt["chunk_count"] == len(chunks)
        assert receipt["content_hash"].startswith("sha256:")
        assert receipt["searchable"] is True
        assert receipt["published_time"]

        # 入库切片与产物逐条对应：条数、顺序、人工修订都要一致（重切分会合并短段）
        expected = [edited_text if item["seq"] == target["seq"] else item["content"] for item in chunks]
        async with async_session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(KbChunkModel)
                        .where(KbChunkModel.publication_id == publication_id)
                        .order_by(KbChunkModel.seq)
                    )
                )
                .scalars()
                .all()
            )
        assert [row.seq for row in rows] == list(range(1, len(expected) + 1))
        assert [row.content for row in rows] == [text.strip() for text in expected]

        # 回执回填产物行：既是页面上的「已写入」，也是重复写入的唯一拦截依据
        after = (await call("GET", f"/api/v1/kb/parse-jobs/detail/{job_id}")).json()["data"]["result"]
        assert after["publication_id"] == publication_id
        assert after["published_time"]

        again = await _publish(job_id, {"title": "重复提交不应再生成一个发布单元"})
        assert again.status_code == 409
        assert publication_id in again.json()["msg"]

        ghost = await _publish("not-exists-job", {})
        assert ghost.status_code == 404
    finally:
        if publication_id:
            await _drop_publication(publication_id)
        await _cleanup(job_id)
