"""kb 解析链路：#01 受理 → 后台解析 → #02 轮询结果。

解析任务跑在请求之外（自开短事务回写状态），所以这里验的是**跨请求的状态迁移**：
受理回执、幂等回放、前置校验归口（4xx 在 202 之前）、以及拉取超限必须立刻中断。
"""

import json
import uuid
from typing import Any

import pytest

from app.config.setting import settings
from app.core.rag.page_render import render_pdf_pages
from app.core.security_signature import build_signed_headers
from app.modules.kb.parse_result import preview
from app.modules_api.base import errors as contract_errors
from app.modules_api.kb.parse import service as parse_service
from app.modules_api.kb.parse.service import MAX_FILE_BYTES, fetch_file
from app.scripts.contract.mock_gsi import FileServer
from app.utils.text_splitter import split_text

from tests.support import call, requires_db


def _parse_payload(job_id: str, file_url: str, **overrides: Any) -> bytes:
    """构造 #01 正文（字段与手册 §六.01 一致）；overrides 用于负向用例。"""
    payload: dict[str, Any] = {
        "job_id": job_id,
        "doc_id": "doc-001",
        "version_id": "v1",
        "file_url": file_url,
        "file_name": "vpn-guide.md",
        "options": {"chunk": True, "language": "zh"},
        "tenant_id": "gsi-default",
        "resource_type": "document",
        "file_size": 1024,
        "metadata": {"title": "VPN 申请操作手册"},
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False).encode()


def _signed_headers(method: str, path: str, *, body: bytes = b"") -> dict[str, str]:
    """每次单发一个新 nonce：幂等回放用例必须绕过「nonce 重放」这层拒绝。"""
    return build_signed_headers(
        settings.CONTRACT_V1_SHARED_SECRET,
        client_id=settings.CONTRACT_V1_CLIENT_ID,
        method=method,
        path=path,
        body=body,
        nonce=f"parse-{uuid.uuid4().hex}",
    )


async def _cleanup_parse_job(job_id: str) -> None:
    """清掉任务行、产物与幂等台账，避免用例互相污染（解析链路没有业务删除入口）。"""
    from sqlalchemy import delete

    from app.core.database import async_session_factory
    from app.modules.kb.parse_job.model import KbParseJobModel
    from app.modules.kb.parse_result.model import KbParseResultModel
    from app.modules.system.idempotency.model import SystemIdempotencyModel

    async with async_session_factory() as session:
        await session.execute(delete(KbParseResultModel).where(KbParseResultModel.job_id == job_id))
        await session.execute(delete(KbParseJobModel).where(KbParseJobModel.uuid == job_id))
        await session.execute(delete(SystemIdempotencyModel).where(SystemIdempotencyModel.biz_id == job_id))
        await session.commit()


@requires_db
async def test_parse_job_accepts_then_reaches_succeeded() -> None:
    """#01 202 受理 → 后台解析 → #02 拿到 succeeded 与完整切片（无向量、pages=[]）。"""
    job_id = f"parse-{uuid.uuid4().hex[:12]}"
    try:
        with FileServer() as server:
            body = _parse_payload(job_id, server.url)
            created = await call(
                "POST", "/v1/parse/jobs", body=body, headers=_signed_headers("POST", "/v1/parse/jobs", body=body)
            )
            assert created.status_code == 202
            receipt = created.json()
            assert receipt["job_id"] == job_id
            assert receipt["status"] in {"queued", "running", "succeeded"}

            detail = await call(
                "GET", f"/v1/parse/jobs/{job_id}", headers=_signed_headers("GET", f"/v1/parse/jobs/{job_id}")
            )
            assert detail.status_code == 200
            payload = detail.json()
            assert payload["status"] == "succeeded"
            assert payload["progress"] == 100
            assert payload["result"]["file_name"] == "vpn-guide.md"
            assert payload["result"]["pages"] == []
            assert payload["result"]["warnings"] == []
            assert payload["result"]["chunks"]
            first_chunk = payload["result"]["chunks"][0]
            assert first_chunk["seq"] == 1
            assert first_chunk["source_type"] == "text"
            assert first_chunk["content"]
    finally:
        await _cleanup_parse_job(job_id)


@requires_db
async def test_parse_job_replay_returns_same_receipt() -> None:
    """同 job_id + 同正文重试直接回放首次 202 回执，不重复建任务/写产物。"""
    job_id = f"parse-{uuid.uuid4().hex[:12]}"
    try:
        with FileServer() as server:
            body = _parse_payload(job_id, server.url)
            first = await call(
                "POST", "/v1/parse/jobs", body=body, headers=_signed_headers("POST", "/v1/parse/jobs", body=body)
            )
            second = await call(
                "POST", "/v1/parse/jobs", body=body, headers=_signed_headers("POST", "/v1/parse/jobs", body=body)
            )
            assert first.status_code == 202
            assert second.status_code == 202
            assert second.json() == first.json()
    finally:
        await _cleanup_parse_job(job_id)


@requires_db
async def test_parse_job_rejects_invalid_requests() -> None:
    """FAQ 条件必填 / 类型白名单 / 声明大小三类前置校验都在 202 之前归口成 4xx。"""
    faq_job = f"parse-{uuid.uuid4().hex[:12]}"
    body = _parse_payload(faq_job, "http://127.0.0.1:9/unused.md", resource_type="faq")
    response = await call(
        "POST", "/v1/parse/jobs", body=body, headers=_signed_headers("POST", "/v1/parse/jobs", body=body)
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == contract_errors.INVALID_REQUEST
    assert "prompt_text" in response.json()["error"]["message"]

    suffix_job = f"parse-{uuid.uuid4().hex[:12]}"
    body = _parse_payload(suffix_job, "http://127.0.0.1:9/unused.exe", file_name="setup.exe")
    response = await call(
        "POST", "/v1/parse/jobs", body=body, headers=_signed_headers("POST", "/v1/parse/jobs", body=body)
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == contract_errors.INVALID_REQUEST
    assert ".exe" in response.json()["error"]["message"]

    size_job = f"parse-{uuid.uuid4().hex[:12]}"
    body = _parse_payload(size_job, "http://127.0.0.1:9/unused.md", file_size=MAX_FILE_BYTES + 1)
    response = await call(
        "POST", "/v1/parse/jobs", body=body, headers=_signed_headers("POST", "/v1/parse/jobs", body=body)
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == contract_errors.PAYLOAD_TOO_LARGE


async def test_fetch_file_stops_at_limit() -> None:
    """实际拉取超过上限立刻中断（不能等整个文件读完再判断，否则 10MB 限制形同虚设）。"""
    with FileServer() as server:
        with pytest.raises(contract_errors.ContractApiError) as exc:
            await fetch_file(server.url, max_bytes=64)
        assert exc.value.code == contract_errors.PAYLOAD_TOO_LARGE


def test_split_text_assigns_section_heading() -> None:
    """分片按起始位置回填 Markdown 章节标题（#02 的 chunks[].heading 依赖它）。"""
    chunks = split_text(
        "# 一级标题\n\n" + "第一段正文内容。" * 5 + "\n\n## 申请流程\n\n" + "第二段正文内容。" * 5,
        chunk_size=40,
        overlap=10,
    )
    headings = [chunk.meta.get("section_title") for chunk in chunks]
    assert "一级标题" in headings
    assert "申请流程" in headings


def test_render_pdf_pages_returns_png() -> None:
    """PDF 页图渲染返回契约所需的页码/宽高，以及可解码的 PNG 字节。"""
    import io

    import pypdfium2 as pdfium

    document = pdfium.PdfDocument.new()
    page = document.new_page(120, 200)
    page.gen_content()
    buffer = io.BytesIO()
    document.save(buffer)
    page.close()
    document.close()

    rendered = render_pdf_pages(buffer.getvalue(), max_pages=1, scale=1)
    assert rendered.total_pages == 1
    assert len(rendered.pages) == 1
    first = rendered.pages[0]
    assert (first.page_no, first.width, first.height) == (1, 120, 200)
    assert first.data.startswith(b"\x89PNG\r\n\x1a\n")


class _FakeS3:
    """对象存储替身：只记录上传并回报可预期的预签名地址，避免用例真的走网络。"""

    def __init__(self) -> None:
        self.uploads: list[tuple[str, int, str | None]] = []

    def upload_bytes(self, data: bytes, object_key: str, *, content_type: str | None = None, **_: Any) -> str:
        self.uploads.append((object_key, len(data), content_type))
        return object_key

    def presign_get(self, object_key: str, expires_in: int | None = None) -> str:
        return f"https://obs.example.com/{object_key}?X-Amz-Expires={expires_in}"


async def test_build_pages_uploads_previews_to_object_storage(monkeypatch) -> None:
    """PDF 页图上传对象存储并返回预签名 URL；object_key 随产物落库，对外投影时被剔除。"""
    import io

    import pypdfium2 as pdfium

    fake = _FakeS3()
    monkeypatch.setattr(settings, "PARSE_PREVIEW_ENABLE", True)
    monkeypatch.setattr(settings, "S3_ENABLE", True)
    monkeypatch.setattr(settings, "PARSE_PREVIEW_SCALE", 1.0)
    monkeypatch.setattr(settings, "PARSE_PREVIEW_TTL_SECONDS", 600)
    monkeypatch.setattr(preview, "get_s3_client", lambda: fake)

    document = pdfium.PdfDocument.new()
    page = document.new_page(120, 200)
    page.gen_content()
    buffer = io.BytesIO()
    document.save(buffer)
    page.close()
    document.close()

    warnings: list[str] = []
    pages = await parse_service._build_pages("job-preview", "guide.pdf", buffer.getvalue(), warnings)
    assert warnings == []
    assert [(key, content_type) for key, _, content_type in fake.uploads] == [
        ("parse-previews/job-preview/page-1.png", "image/png")
    ]
    assert pages and (pages[0]["width"], pages[0]["height"]) == (120, 200)
    assert pages[0]["object_key"] == "parse-previews/job-preview/page-1.png"
    assert pages[0]["image_url"] == (
        "https://obs.example.com/parse-previews/job-preview/page-1.png?X-Amz-Expires=600"
    )

    # 对外投影：按 object_key 重签，且不把内部字段抛给调用方
    refreshed = preview.refresh_pages(pages, ttl_seconds=60)
    assert "object_key" not in refreshed[0]
    assert refreshed[0]["image_url"].endswith("?X-Amz-Expires=60")


def test_build_suggested_prefers_page_metadata() -> None:
    """建议元数据优先复用页面已填值，缺失时从正文标题兜底。"""
    suggested, doc_type = parse_service.build_suggested(
        {"title": "VPN 申请操作手册", "summary": "开通说明", "tags": ["VPN", " "], "doc_type": "SOP"},
        "# 正文标题\n\n正文内容",
        "guide.md",
    )
    assert doc_type == "SOP"
    assert suggested == {"title": "VPN 申请操作手册", "summary": "开通说明", "tags": ["VPN"]}


@requires_db
async def test_faq_parse_uses_prompt_and_returns_questions(monkeypatch) -> None:
    """FAQ 分支：按 prompt_text 调模型，question 落 heading、answer 落 content。"""
    async def fake_chat(self, messages, **kwargs) -> str:
        return '[{"question": "如何申请 VPN？", "answer": "在服务台提交 VPN 申请，由负责人审核后开通账号。"}]'

    monkeypatch.setattr("app.modules_api.kb.parse.service.LlmClient.chat", fake_chat)

    job_id = f"parse-{uuid.uuid4().hex[:12]}"
    try:
        with FileServer() as server:
            body = _parse_payload(
                job_id,
                server.url,
                resource_type="faq",
                prompt_text="从文档中提取常见问题及对应答案，答案必须有原文依据。",
                model_key="default",
            )
            created = await call(
                "POST", "/v1/parse/jobs", body=body, headers=_signed_headers("POST", "/v1/parse/jobs", body=body)
            )
            assert created.status_code == 202

            detail = await call(
                "GET", f"/v1/parse/jobs/{job_id}", headers=_signed_headers("GET", f"/v1/parse/jobs/{job_id}")
            )
            payload = detail.json()
            assert payload["status"] == "succeeded"
            chunk = payload["result"]["chunks"][0]
            assert chunk["heading"] == "如何申请 VPN？"
            assert "服务台" in chunk["content"]
    finally:
        await _cleanup_parse_job(job_id)


@requires_db
async def test_parse_failure_is_task_result(monkeypatch) -> None:
    """解析失败属于任务结果：``#02`` 仍返回 HTTP 200，并带 error_code/error_message。"""
    from app.core.exceptions import CustomException

    async def boom(*args, **kwargs):
        raise CustomException(msg="解析器不可用。", status_code=500)

    monkeypatch.setattr(parse_service, "parse_and_chunk", boom)

    job_id = f"parse-{uuid.uuid4().hex[:12]}"
    try:
        with FileServer() as server:
            body = _parse_payload(job_id, server.url)
            created = await call(
                "POST", "/v1/parse/jobs", body=body, headers=_signed_headers("POST", "/v1/parse/jobs", body=body)
            )
            assert created.status_code == 202

            detail = await call(
                "GET", f"/v1/parse/jobs/{job_id}", headers=_signed_headers("GET", f"/v1/parse/jobs/{job_id}")
            )
            assert detail.status_code == 200
            payload = detail.json()
            assert payload["status"] == "failed"
            assert payload["error_code"] == contract_errors.PARSE_FAILED
            assert payload["error_message"] == "解析器不可用。"
    finally:
        await _cleanup_parse_job(job_id)


@requires_db
async def test_parse_result_persists_pages_and_suggested_doc_type() -> None:
    """成功产物把 pages 与 suggested_doc_type 一并落库，并能从 #02 快照读回。"""
    job_id = f"parse-{uuid.uuid4().hex[:12]}"
    try:
        service = parse_service.PARSE_JOB_SERVICE
        await service.create_job(job_id=job_id, resource_type="document", file_meta={"file_name": "guide.pdf"})
        await service.complete(
            job_id,
            chunks=[{"seq": 1, "content": "正文", "source_type": "text"}],
            pages=[
                {
                    "page_no": 1,
                    "width": 240,
                    "height": 400,
                    "image_url": "https://obs.example.com/parse-previews/job/page-1.png?X-Amz-Signature=x",
                }
            ],
            suggested={"title": "VPN 手册", "tags": ["VPN"]},
            suggested_doc_type="SOP",
            warnings=["仅返回前 1 页预览图。"],
            content_hash="hash",
        )
        snapshot = await service.get_snapshot(job_id)
        assert snapshot is not None
        assert snapshot.status == "succeeded"
        assert snapshot.pages == [
            {
                "page_no": 1,
                "width": 240,
                "height": 400,
                "image_url": "https://obs.example.com/parse-previews/job/page-1.png?X-Amz-Signature=x",
            }
        ]
        assert snapshot.suggested_doc_type == "SOP"
        assert snapshot.warnings == ["仅返回前 1 页预览图。"]
    finally:
        await _cleanup_parse_job(job_id)
