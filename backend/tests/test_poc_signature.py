"""POC `/v1` 的最小回归网（T0.1 签名 + T0.3 生成后处理）。

覆盖口径：
1. 签名原文形状（7 行、LF、末尾无换行）与自签自验闭环 —— 这两处写错，联调时会以「签名失败」
   的形式暴露，却很难定位到底是原文、编码还是时间戳；
2. 验签失败的四类原因各一条（缺头/时间戳越窗/签名不符/nonce 重放），保证失败原因不被混淆；
3. `authorization` 窗口校验（跨度超限 400、过期 401）；
4. `quote` 必须是切片正文的逐字子串、`citation_id` 与答案编号一一对应 —— 契约硬要求。
"""

import hashlib
from typing import Any

import pytest

from app.core.logger import logger
from app.core.rich_print import pretty_log
from app.poc.chat import build_citations, build_source_snapshots, pick_quote
from app.poc.errors import PocApiError
from app.poc.schemas import AuthorizationSchema, request_digest
from app.poc.signature import (
    SIGNATURE_VERSION,
    NonceRegistry,
    build_signed_headers,
    canonical_string,
    verify_authorization_window,
    verify_signed_request,
)

SECRET = "unit-test-secret"
CLIENT_ID = "gsi-service"
NOW_MS = 1_700_000_000_000
PATH = "/v1/knowledge/search"
BODY = b'{"request_id":"request-001"}'


def _headers(*, timestamp_ms: int = NOW_MS, nonce: str = "unit-test-nonce-0001", secret: str = SECRET) -> dict[str, str]:
    return build_signed_headers(
        secret,
        client_id=CLIENT_ID,
        method="POST",
        path=PATH,
        body=BODY,
        timestamp_ms=timestamp_ms,
        nonce=nonce,
    )


def _verify(
    headers: dict[str, str],
    *,
    registry: NonceRegistry | None = None,
    body: bytes = BODY,
    current_ms: int = NOW_MS,
):
    return verify_signed_request(
        headers,
        method="POST",
        path=PATH,
        query="",
        body=body,
        secret=SECRET,
        client_id=CLIENT_ID,
        current_ms=current_ms,
        registry=registry if registry is not None else NonceRegistry(),
    )


def _expect(exc: pytest.ExceptionInfo[PocApiError], code: str, status_code: int) -> None:
    assert exc.value.code == code
    assert exc.value.status_code == status_code


# ── 签名原文与闭环 ────────────────────────────────────────────────────


def test_canonical_string_is_seven_lines_without_trailing_newline() -> None:
    """签名原文固定 7 行、LF 分隔、末尾不带换行，方法统一大写、末行为 body 的 SHA-256。

    多一行、用 CRLF 或末尾多一个 LF，联调方算出的 hex 都会和服务端对不上。
    """
    canonical = canonical_string(
        client_id=CLIENT_ID, timestamp=str(NOW_MS), nonce="unit-test-nonce-0001", method="post", target=PATH, body=BODY
    )
    logger.info(f"canonical: {canonical}")
    lines = canonical.split("\n")
    assert len(lines) == 7
    assert lines[0] == SIGNATURE_VERSION
    assert lines[4] == "POST"  # 方法必须大写
    assert lines[6] == hashlib.sha256(BODY).hexdigest()
    assert not canonical.endswith("\n")


def test_signed_request_roundtrip() -> None:
    """同一 secret 自签自验必须通过，并回传 client_id、时间戳与 64 位签名摘要。"""
    secret_headers = _headers()
    logger.info(f"secret_headers: {pretty_log(secret_headers)}")
    context = _verify(secret_headers)
    logger.info(f"verification context: {context}")
    assert context.client_id == CLIENT_ID
    assert context.timestamp_ms == NOW_MS
    assert len(context.signature_digest) == 64


def test_tampered_body_is_rejected() -> None:
    """请求体被改动后签名失效，按 401 unauthorized 拒绝。"""
    with pytest.raises(PocApiError) as exc:
        _verify(_headers(), body=b'{"request_id":"request-999"}')
    _expect(exc, "unauthorized", 401)


def test_missing_header_is_rejected() -> None:
    """缺任意签名头都归为 401 unauthorized，而不是参数校验的 400。"""
    headers = _headers()
    headers.pop("X-AI-Nonce")
    with pytest.raises(PocApiError) as exc:
        _verify(headers)
    _expect(exc, "unauthorized", 401)


def test_timestamp_out_of_window_is_rejected() -> None:
    """时间戳超出 ±5 分钟窗口（这里早 300001ms）按 401 拒绝，防重放。"""
    with pytest.raises(PocApiError) as exc:
        _verify(_headers(timestamp_ms=NOW_MS - 300_001))
    _expect(exc, "unauthorized", 401)


def test_bad_nonce_format_is_rejected() -> None:
    """nonce 长度/格式不合规同样按 401 拒绝，避免脏值进注册表。"""
    with pytest.raises(PocApiError) as exc:
        _verify(_headers(nonce="short"))
    _expect(exc, "unauthorized", 401)


def test_nonce_replay_is_rejected() -> None:
    """同一 nonce 第二次提交视为重放，共用注册表时按 401 拒绝。"""
    registry = NonceRegistry()
    _verify(_headers(), registry=registry)  # 首次通过并登记

    with pytest.raises(PocApiError) as exc:
        _verify(_headers(), registry=registry)
    _expect(exc, "unauthorized", 401)


def test_nonce_registry_sweeps_expired_entries() -> None:
    """注册表在下一次登记前清掉过期条目，size 只统计未过期的 nonce。"""
    registry = NonceRegistry()
    assert registry.register(CLIENT_ID, "nonce-expire-0001", expires_at_ms=NOW_MS - 1, current_ms=NOW_MS)
    # 清扫在「下一次登记前」执行：过期条目不影响新 nonce，也不会一直堆在字典里
    assert registry.register(CLIENT_ID, "nonce-fresh-0001", expires_at_ms=NOW_MS + 300_000, current_ms=NOW_MS)
    assert registry.size() == 1


# ── authorization 窗口 ────────────────────────────────────────────────


def test_authorization_ttl_over_limit_is_rejected() -> None:
    """authorization 的签发-过期跨度超过窗口上限，按 400 invalid_request 拒绝（错在请求本身）。"""
    with pytest.raises(PocApiError) as exc:
        verify_authorization_window(scope_id="scope-001", issued_at=NOW_MS, expires_at=NOW_MS + 120_001, current_ms=NOW_MS)
    _expect(exc, "invalid_request", 400)


def test_authorization_expired_is_rejected() -> None:
    """authorization 已过期按 401 unauthorized 拒绝，与跨度超限的 400 区分开。"""
    with pytest.raises(PocApiError) as exc:
        verify_authorization_window(scope_id="scope-001", issued_at=NOW_MS - 60_000, expires_at=NOW_MS - 1, current_ms=NOW_MS)
    _expect(exc, "unauthorized", 401)


def test_authorization_valid_window_passes() -> None:
    """跨度合法且未过期时校验通过，不抛任何异常。"""
    verify_authorization_window(scope_id="scope-001", issued_at=NOW_MS, expires_at=NOW_MS + 60_000, current_ms=NOW_MS)


# ── 生成后处理 ────────────────────────────────────────────────────────


def _hit(index: int, content: str) -> dict[str, Any]:
    return {
        "resource_type": "document",
        "resource_id": "doc-001",
        "publication_id": f"pub-{index}",
        "record_id": f"record-{index}",
        "version_id": "1",
        "revision": 101,
        "knowledge_no": "kb-0001",
        "chunk_id": f"record-{index}:1",
        "title": "vpn 申请操作手册",
        "content": content,
        "snippet": content[:20],
        "score": 0.9,
    }


def test_pick_quote_returns_verbatim_substring() -> None:
    """quote 必须是切片正文的逐字子串，不能是模型改写过的句子。"""
    content = "第一句无关内容。在服务台提交 vpn 申请，由负责人审核后开通账号。最后一句补充说明。"
    quote = pick_quote("请在服务台提交 vpn 申请，由负责人审核后开通账号。", content)
    assert quote in content
    assert "服务台提交 vpn 申请" in quote


def test_pick_quote_falls_back_to_head_on_empty_sentences() -> None:
    """正文为空时退化为空字符串，不能抛异常让整条回答失败。"""
    assert pick_quote("任意问题", "") == ""


def test_build_citations_maps_marks_and_ignores_out_of_range() -> None:
    """answer 中的 [1][2] 依次映射到 hits，产出 c1/c2 并回填 chunk_id 与逐字 quote。

    越界标记（如 [9]）直接丢弃，不允许凭空造出一条 citation。
    """
    hits = [_hit(1, "在服务台提交 vpn 申请。"), _hit(2, "需要负责人审核。")]
    answer = "请在服务台提交 vpn 申请。[1] 由负责人审核后方可开通。[2] 另外请参考 [9]。"

    citations = build_citations(answer, hits)
    assert [item["index"] for item in citations] == [1, 2]
    assert [item["citation_id"] for item in citations] == ["c1", "c2"]
    for item, hit in zip(citations, hits, strict=True):
        assert item["chunk_id"] == hit["chunk_id"]
        assert item["quote"] in hit["content"]


def test_build_citations_keeps_empty_list_when_model_cites_nothing() -> None:
    """模型没标任何编号时 citations 保持空列表，不能把 hits 硬塞进去。"""
    assert build_citations("没有标注来源的回答。", [_hit(1, "正文。")]) == []


def test_build_source_snapshots_dedupes_by_snapshot() -> None:
    """source 快照按 publication/record/version 去重，且只暴露契约规定的 6 个字段。"""
    hits = [_hit(1, "a"), _hit(1, "b"), _hit(2, "c")]
    snapshots = build_source_snapshots(hits)
    assert len(snapshots) == 2
    assert set(snapshots[0]) == {"resource_type", "resource_id", "publication_id", "record_id", "version_id", "revision"}


def test_request_digest_is_stable_and_sensitive() -> None:
    """authorization 摘要必须稳定（同输入同值）又能区分 scope，用于幂等判定。"""
    first = AuthorizationSchema(scope_id="scope-001", issued_at=NOW_MS, expires_at=NOW_MS + 60_000)
    again = AuthorizationSchema(scope_id="scope-001", issued_at=NOW_MS, expires_at=NOW_MS + 60_000)
    other = AuthorizationSchema(scope_id="scope-002", issued_at=NOW_MS, expires_at=NOW_MS + 60_000)
    assert request_digest(first) == request_digest(again)
    assert request_digest(first) != request_digest(other)
