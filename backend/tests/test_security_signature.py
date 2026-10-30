"""接口安全头公共层回归（`app/core/security_signature.py` + `app/contract/signature.py`）。

覆盖口径：
1. **算法只有一份**：原文拼装、时间窗、nonce 格式、`authorization` 跨度都在公共层，
   契约适配层不持有私有实现；
2. **失败关闭**：共享密钥留空时公共层直接拒绝，契约层归 503 `AUTHORIZATION_UNAVAILABLE`；
3. **nonce 时序**：`verify_request_signature` 先验签再登记，同一组头第二次到达判重放；
4. **口径归口**：缺头 401 `UNAUTHORIZED`、`authorization` 跨度超限 400、已过期 401、缺 scope 400 `SCOPE_REQUIRED`；
5. **配置一组**：`setting.py` 的 `CONTRACT_*` 是唯一来源（POC 下线后不再有 `POC_*` 签名项）。
"""

import uuid
from pathlib import Path

import pytest

from app.config.setting import Settings, settings
from app.contract import errors as contract_errors
from app.contract.signature import verify_authorization as contract_verify_authorization
from app.contract.signature import verify_signature as contract_verify_signature
from app.core.rich_print import pretty_log
from app.core.security_signature import (
    HEADER_NONCE,
    HEADER_SIGNATURE,
    InMemoryNonceLedger,
    SignatureFailure,
    build_signed_headers,
    body_digest,
    check_authorization_window,
    check_signature,
    now_ms,
    signature_rule_doc,
    verify_request_signature,
)

SECRET = "shared-rule-test-secret"
CLIENT_ID = "gsi-service"
NOW_MS = 1_700_000_000_000
PATH = "/v1/knowledge/search"
BODY = b'{"request_id":"req-shared-001"}'


def _headers(*, nonce: str = "shared-rule-nonce-0001", timestamp_ms: int = NOW_MS) -> dict[str, str]:
    return build_signed_headers(
        SECRET,
        client_id=CLIENT_ID,
        method="POST",
        path=PATH,
        body=BODY,
        timestamp_ms=timestamp_ms,
        nonce=nonce,
    )

# ── 个人测试用例 ──────────────────────────────────────
# 调试用：生成一组可直接粘贴到 curl / Apifox / Postman 的签名头。
# 四处必须与服务端完全一致，否则一律 401「服务签名认证失败」：
# 1. 密钥 = env/.env.<ENVIRONMENT> 的 CONTRACT_V1_SHARED_SECRET（不要再硬编码另一份）；
# 2. path = 真实请求路径，服务端用 request.url.path 比对，传空串必错；
# 3. body 原始字节：多行 JSON 完全支持，但缩进/换行/末尾换行差一个字节就验不过，
#    所以统一从 debug_body.json 读字节，客户端也用同一个文件发（curl 必须 --data-binary @file，
#    -d / --data-raw 会把换行删掉）；
# 4. 五个头都要有值，缺值会先被判「缺少签名请求头」。
DEBUG_PATH = "/v1/knowledge/search"
DEBUG_BODY_FILE = Path(__file__).with_name("debug_body.json")
DEBUG_BODY = DEBUG_BODY_FILE.read_bytes() if DEBUG_BODY_FILE.exists() else b'{"request_id":"req-shared-001"}'


def test_generate_custom_headers():
    """用于生成自定义的请求头，方便 api 接口调用"""
    secret = settings.CONTRACT_V1_SHARED_SECRET
    assert secret.strip(), "CONTRACT_V1_SHARED_SECRET 为空：服务端会返回 503，先在 env/.env.dev 配置密钥"
    signed_headers = build_signed_headers(
        secret,
        client_id=settings.CONTRACT_V1_CLIENT_ID,
        method="POST",
        path=DEBUG_PATH,
        body=DEBUG_BODY,
        timestamp_ms=now_ms(),  # 真实当前 Unix 毫秒，服务端只接受 ±5 分钟窗口内的时间戳
        nonce=uuid.uuid4().hex,  # 每次新生成：固定 nonce 第二次到达会被判重放
    )
    print("==== 自定义安全头测试 ====")
    print("== 请求体 ==")
    print(DEBUG_BODY)
    print("== 请求体长度 ==")
    print(len(DEBUG_BODY))
    print("== 请求体 SHA-256 ==")
    print(body_digest(DEBUG_BODY))
    print("== 自定义安全头 ==")
    print(pretty_log(signed_headers))
    print("== apifox 请求头 ==")
    # 逐行 `头名:值`（冒号后不留空格），Apifox 的「批量编辑 / 原始文本导入」可直接粘贴
    print("\n".join(f"{name}:{value}" for name, value in signed_headers.items()))
    print("== curl 命令 ==")
    # 多行 body 的核对依据：客户端发完后用字节数与摘要确认发的是同一份数据
    print(f"\n# body {len(DEBUG_BODY)} 字节 / SHA-256 {body_digest(DEBUG_BODY)}")
    print(
        f"curl --location --request POST 'http://localhost:{settings.PORT}{DEBUG_PATH}' \\\n"
        + "".join(f"  --header '{name}:{value}' \\\n" for name, value in signed_headers.items())
        + "  --header 'Content-Type: application/json' \\\n"
        + f"  --data-binary @{DEBUG_BODY_FILE}"
    )


# ── 公共层生成的头，契约适配层必须验过 ────────────────────────────────


def test_shared_headers_verify_through_contract_adapter() -> None:
    """公共层生成的头由契约适配层验过，并只回传 12 位签名摘要（审计不留原文）。"""
    contract = contract_verify_signature(
        _headers(),
        method="POST",
        path=PATH,
        query="",
        body=BODY,
        secret=SECRET,
        client_id=CLIENT_ID,
        current_ms=NOW_MS,
    )
    assert contract.client_id == CLIENT_ID
    assert contract.timestamp_ms == NOW_MS
    assert len(contract.signature_digest) == 12


def test_canonical_algorithm_is_shared_not_copied() -> None:
    """契约适配层不再自持算法：模块里没有同名私有实现，公共层只此一份。"""
    import app.contract.signature as contract_signature
    import app.core.security_signature as security_signature

    assert not hasattr(contract_signature, "NONCE_PATTERN"), "contract 仍在维护自己的 nonce 正则"
    assert hasattr(security_signature, "NONCE_PATTERN"), "公共层应持有唯一的 nonce 正则"


# ── 失败口径 ───────────────────────────────────────────────────────


def test_missing_header_maps_to_contract_code() -> None:
    """缺头：契约层点名缺哪个头 + 401 `UNAUTHORIZED`（不是 400 参数校验）。"""
    headers = _headers()
    headers.pop(HEADER_NONCE)

    with pytest.raises(contract_errors.ContractApiError) as contract_exc:
        contract_verify_signature(
            headers,
            method="POST",
            path=PATH,
            query="",
            body=BODY,
            secret=SECRET,
            client_id=CLIENT_ID,
            current_ms=NOW_MS,
        )
    assert (contract_exc.value.code, contract_exc.value.status_code) == (contract_errors.UNAUTHORIZED, 401)
    assert HEADER_NONCE in contract_exc.value.message


def test_unconfigured_secret_fails_closed_at_shared_layer() -> None:
    """共享密钥留空：公共层直接拒绝，不再用空密钥算 HMAC（否则等于关掉鉴权）。"""
    with pytest.raises(Exception):
        verify_request_signature(
            _headers(),
            method="POST",
            path=PATH,
            query="",
            body=BODY,
            secret="",
            client_id=CLIENT_ID,
            clock_skew_ms=300_000,
            current_ms=NOW_MS,
            ledger=InMemoryNonceLedger(),
        )

    with pytest.raises(contract_errors.ContractApiError) as contract_exc:
        contract_verify_signature(
            _headers(),
            method="POST",
            path=PATH,
            query="",
            body=BODY,
            secret="",
            client_id=CLIENT_ID,
            current_ms=NOW_MS,
        )
    # 契约层口径：依赖不可用是可重试的 503，不是「签名不对」的 401
    assert contract_exc.value.code == contract_errors.AUTHORIZATION_UNAVAILABLE
    assert contract_exc.value.status_code == 503
    assert contract_exc.value.retryable is True


# ── nonce 时序：先验签再登记 ──────────────────────────────────────────


def test_nonce_is_registered_only_after_signature_passes() -> None:
    """签名不符时不登记 nonce：否则伪造请求能抢先占掉合法 nonce。"""
    ledger = InMemoryNonceLedger()
    forged = {**_headers(), HEADER_SIGNATURE: "0" * 64}

    with pytest.raises(Exception, match="服务签名认证失败"):
        verify_request_signature(
            forged,
            method="POST",
            path=PATH,
            query="",
            body=BODY,
            secret=SECRET,
            client_id=CLIENT_ID,
            clock_skew_ms=300_000,
            current_ms=NOW_MS,
            ledger=ledger,
        )
    assert ledger.size() == 0

    verify_request_signature(
        _headers(),
        method="POST",
        path=PATH,
        query="",
        body=BODY,
        secret=SECRET,
        client_id=CLIENT_ID,
        clock_skew_ms=300_000,
        current_ms=NOW_MS,
        ledger=ledger,
    )
    assert ledger.size() == 1

    # 同一组头第二次到达：验签通过但登记失败 → 重放
    with pytest.raises(Exception) as replay_exc:
        verify_request_signature(
            _headers(),
            method="POST",
            path=PATH,
            query="",
            body=BODY,
            secret=SECRET,
            client_id=CLIENT_ID,
            clock_skew_ms=300_000,
            current_ms=NOW_MS,
            ledger=ledger,
        )
    assert getattr(replay_exc.value, "reason", None) is SignatureFailure.NONCE_REUSED


# ── authorization 归口 ───────────────────────────────────────────────


def test_authorization_failures_split_codes_by_cause() -> None:
    """`authorization`：跨度超限 400 `INVALID_REQUEST`、已过期 401 `UNAUTHORIZED`。"""
    with pytest.raises(contract_errors.ContractApiError) as contract_ttl:
        contract_verify_authorization(
            {"scope_id": "s", "issued_at": NOW_MS, "expires_at": NOW_MS + 120_001}, current_ms=NOW_MS
        )
    assert (contract_ttl.value.code, contract_ttl.value.status_code) == (contract_errors.INVALID_REQUEST, 400)

    with pytest.raises(contract_errors.ContractApiError) as contract_expired:
        contract_verify_authorization(
            {"scope_id": "s", "issued_at": NOW_MS - 60_000, "expires_at": NOW_MS - 1}, current_ms=NOW_MS
        )
    assert (contract_expired.value.code, contract_expired.value.status_code) == (contract_errors.UNAUTHORIZED, 401)


def test_missing_scope_keeps_contract_specific_code() -> None:
    """缺 scope_id：契约层用专用码 `SCOPE_REQUIRED`。"""
    with pytest.raises(contract_errors.ContractApiError) as contract_exc:
        contract_verify_authorization({"issued_at": NOW_MS, "expires_at": NOW_MS + 1_000}, current_ms=NOW_MS)
    assert contract_exc.value.code == contract_errors.SCOPE_REQUIRED


def test_shared_window_check_is_pure_and_raises_neutral_error() -> None:
    """公共层只判定不翻译：合法窗口不抛，非法抛中性异常（供各接入层映射）。"""
    check_authorization_window(scope_id="s", issued_at=NOW_MS, expires_at=NOW_MS + 60_000, current_ms=NOW_MS)
    with pytest.raises(Exception, match="120 秒"):
        check_authorization_window(scope_id="s", issued_at=NOW_MS, expires_at=NOW_MS + 300_000, current_ms=NOW_MS)


# ── 配置口径 ───────────────────────────────────────────────────────


def test_contract_reads_one_signature_config_group(monkeypatch: pytest.MonkeyPatch) -> None:
    """签名参数只有 `CONTRACT_*` 一组：POC 期重复项已随阶段 0 删除，改配置即时生效。"""
    for name in ("POC_V1_CLIENT_ID", "POC_V1_SHARED_SECRET", "POC_V1_CLOCK_SKEW_MS", "POC_V1_ENABLE"):
        assert name not in Settings.model_fields, f"{name} 应随 POC 下线一并删除"

    monkeypatch.setattr(settings, "CONTRACT_V1_CLIENT_ID", "switched-client")
    monkeypatch.setattr(settings, "CONTRACT_V1_SHARED_SECRET", SECRET)
    # 契约适配层不传 client_id/secret 时取的是共用配置
    headers = build_signed_headers(
        SECRET,
        client_id="switched-client",
        method="POST",
        path=PATH,
        body=BODY,
        timestamp_ms=NOW_MS,
        nonce="shared-cfg-nonce",
    )
    context = contract_verify_signature(
        headers,
        method="POST",
        path=PATH,
        query="",
        body=BODY,
        current_ms=NOW_MS,
    )
    assert context.client_id == "switched-client"


def test_signature_rule_doc_only_differs_by_error_code() -> None:
    """OpenAPI 说明共用同一段文字，只有 401 的码值口径可替换。"""
    contract_doc = signature_rule_doc()
    lower_doc = signature_rule_doc(unauthorized_code="unauthorized")
    assert "401 `UNAUTHORIZED`" in contract_doc
    assert "401 `unauthorized`" in lower_doc
    for fragment in ("HMAC-SHA256", "7 行", "LF", "SHA-256"):
        assert fragment in contract_doc and fragment in lower_doc


def test_check_signature_reports_clock_skew_reason() -> None:
    """公共层把失败原因做成枚举：接入层按 reason 映射，不靠匹配话术。"""
    with pytest.raises(Exception) as exc:
        check_signature(
            _headers(),
            method="POST",
            path=PATH,
            query="",
            body=BODY,
            secret=SECRET,
            client_id=CLIENT_ID,
            clock_skew_ms=300_000,
            current_ms=NOW_MS + 300_001,
        )
    assert getattr(exc.value, "reason", None) is SignatureFailure.CLOCK_SKEW
