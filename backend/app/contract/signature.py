"""HMAC 四头签名与 `authorization` 时效校验（T1.1 生产化）。

契约口径（手册 §四「公共请求头与签名」）：

签名原文 7 行、LF 连接、**末尾无换行**：

    v1
    {X-AI-Client-Id}
    {X-AI-Timestamp}
    {X-AI-Nonce}
    {大写 HTTP 方法}
    {原始路径及可选查询字符串}
    {请求正文原始字节的 SHA-256 小写十六进制摘要}

签名 = ``HMAC-SHA256(共享密钥, 原文)`` 的 64 位小写十六进制。

本模块与 POC `app/poc/signature.py` 的关系：
1. 算法口径同源（POC 冒烟已验证），但**代码不复用**——POC 是验证骨架、随 T1.x 下线；
2. 本模块只做**纯计算与校验**：nonce 的原子登记要落库，放在 `nonce.py`；审计留痕放在 `deps.py`；
3. 验签必须拿**原始字节**算摘要，不能先解析 JSON 再序列化（键序、空格、中文转义都会变），
   所以验签在依赖里、在 Pydantic 之前执行；
4. 比较用 ``hmac.compare_digest``，任何失败都只回 ``UNAUTHORIZED`` + 固定话术，不回显期望值。
"""

import hashlib
import hmac
import re
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from app.config.setting import settings

from .errors import INVALID_REQUEST, SCOPE_REQUIRED, UNAUTHORIZED, ContractApiError

SIGNATURE_VERSION = "v1"
HEADER_VERSION = "X-AI-Signature-Version"
HEADER_CLIENT_ID = "X-AI-Client-Id"
HEADER_TIMESTAMP = "X-AI-Timestamp"
HEADER_NONCE = "X-AI-Nonce"
HEADER_SIGNATURE = "X-AI-Signature"

# 五个签名头的规范清单：OpenAPI 声明、缺头判定、审计 headers_present 都以它为准（增删头只改一处）
SIGNATURE_HEADERS: Final[tuple[str, ...]] = (
    HEADER_VERSION,
    HEADER_CLIENT_ID,
    HEADER_TIMESTAMP,
    HEADER_NONCE,
    HEADER_SIGNATURE,
)

# 契约：16–128 位字母、数字、下划线或连字符
_NONCE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16,128}$")


def now_ms() -> int:
    """当前 Unix 毫秒（统一入口，便于测试注入固定时钟）。"""
    return int(time.time() * 1000)


def body_digest(body: bytes) -> str:
    """请求正文原始字节的 SHA-256（小写十六进制）；GET 对空字节序列求摘要。"""
    return hashlib.sha256(body or b"").hexdigest()


def canonical_string(
    *,
    client_id: str,
    timestamp: str,
    nonce: str,
    method: str,
    target: str,
    body: bytes,
) -> str:
    """拼签名原文：7 行、LF 连接、末尾无换行（多一个字符签名就对不上）。"""
    return "\n".join([SIGNATURE_VERSION, client_id, timestamp, nonce, method.upper(), target, body_digest(body)])


def compute_signature(secret: str, canonical: str) -> str:
    """HMAC-SHA256 输出 64 位小写十六进制。"""
    return hmac.new(secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()


def request_target(path: str, query: str) -> str:
    """签名原文第 6 行：原始路径 + 可选查询字符串（不含 scheme/host）。"""
    return f"{path}?{query}" if query else path


@dataclass(frozen=True)
class SignatureMaterial:
    """验签通过后的材料：只留客户端标识与签名摘要（审计存摘要，不存原文与密钥）。"""

    client_id: str
    nonce: str
    timestamp_ms: int
    timestamp_deviation_ms: int
    signature_digest: str
    headers_present: tuple[str, ...]


def headers_present(headers: Mapping[str, str]) -> tuple[str, ...]:
    """当前请求实际带了哪几个签名头（审计 `security_check.headers_present` 用）。"""
    return tuple(name for name in SIGNATURE_HEADERS if (headers.get(name) or "").strip())


def parse_signature_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """取五个签名头的去空白值；缺任意一个都按 401 拒绝（不是 400 参数校验）。"""
    values = {name: (headers.get(name) or "").strip() for name in SIGNATURE_HEADERS}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise ContractApiError(UNAUTHORIZED, f"缺少签名请求头：{'、'.join(missing)}。")
    return values


def verify_signature(
    headers: Mapping[str, str],
    *,
    method: str,
    path: str,
    query: str,
    body: bytes,
    secret: str | None = None,
    client_id: str | None = None,
    clock_skew_ms: int | None = None,
    current_ms: int | None = None,
) -> SignatureMaterial:
    """按契约顺序验签：缺头 → 版本/客户端 → 时间窗 → nonce 格式 → 签名比对。

    **不含 nonce 防重放**：那一步要落库原子登记，见 `nonce.NonceStore.register`。
    先验签再登记，否则伪造请求可以抢占合法 nonce。
    """
    expected_client_id = client_id if client_id is not None else settings.CONTRACT_V1_CLIENT_ID
    shared_secret = secret if secret is not None else settings.CONTRACT_V1_SHARED_SECRET
    skew = clock_skew_ms if clock_skew_ms is not None else settings.CONTRACT_CLOCK_SKEW_MS
    stamp_now = current_ms if current_ms is not None else now_ms()
    values = parse_signature_headers(headers)

    version = values[HEADER_VERSION]
    header_client = values[HEADER_CLIENT_ID]
    timestamp = values[HEADER_TIMESTAMP]
    nonce = values[HEADER_NONCE]
    signature = values[HEADER_SIGNATURE]

    if version != SIGNATURE_VERSION:
        raise ContractApiError(UNAUTHORIZED, f"不支持的签名版本：{version}（当前仅 {SIGNATURE_VERSION}）。")
    if header_client != expected_client_id:
        raise ContractApiError(UNAUTHORIZED, "客户端标识无效。")
    if not timestamp.isdigit() or len(timestamp) != 13:
        raise ContractApiError(UNAUTHORIZED, "时间戳须为 13 位 Unix 毫秒。")

    stamp_ms = int(timestamp)
    deviation_ms = stamp_now - stamp_ms
    if abs(deviation_ms) > skew:
        raise ContractApiError(UNAUTHORIZED, "请求时间戳超出允许窗口，请校准时钟后重新签名。")
    if not _NONCE_PATTERN.match(nonce):
        raise ContractApiError(UNAUTHORIZED, "nonce 须为 16–128 位字母、数字、下划线或连字符。")

    canonical = canonical_string(
        client_id=header_client,
        timestamp=timestamp,
        nonce=nonce,
        method=method,
        target=request_target(path, query),
        body=body,
    )
    expected_signature = compute_signature(shared_secret, canonical)
    if not hmac.compare_digest(expected_signature, signature):
        raise ContractApiError(UNAUTHORIZED, "服务签名认证失败，请检查签名、时间戳和 nonce。")

    return SignatureMaterial(
        client_id=header_client,
        nonce=nonce,
        timestamp_ms=stamp_ms,
        timestamp_deviation_ms=deviation_ms,
        signature_digest=signature[:8] + signature[-4:],
        headers_present=headers_present(headers),
    )


def verify_authorization(
    authorization: Mapping[str, object] | None,
    *,
    current_ms: int | None = None,
    max_ttl_ms: int | None = None,
) -> None:
    """校验正文里的 `authorization {scope_id, issued_at, expires_at}`。

    契约只写「最长 120 秒，过期拒绝」，没给专用错误码（POC 疑点 Q1/Q2）：
    - 缺块 → 400 `SCOPE_REQUIRED`（手册 §八 明示该码表示缺少范围）；
    - 跨度超限 / 字段非法 → 400 `INVALID_REQUEST`（错在请求本身，重试无意义）；
    - 已过期 → 401 `UNAUTHORIZED`（凭据失效语义，要求 GSI 重签）。
    `authorization` 是可选块的接口（如 #08 创建会话）传 None 时直接跳过。
    """
    if authorization is None:
        return
    if not isinstance(authorization, Mapping):
        raise ContractApiError(INVALID_REQUEST, "authorization 必须是对象。")

    scope_id = str(authorization.get("scope_id") or "").strip()
    if not scope_id:
        raise ContractApiError(SCOPE_REQUIRED, "缺少 authorization.scope_id。")

    issued_at = authorization.get("issued_at")
    expires_at = authorization.get("expires_at")
    if not isinstance(issued_at, int) or not isinstance(expires_at, int):
        raise ContractApiError(INVALID_REQUEST, "authorization.issued_at / expires_at 须为 Unix 毫秒整数。")
    if expires_at <= issued_at:
        raise ContractApiError(INVALID_REQUEST, "authorization.expires_at 必须大于 issued_at。")

    ttl_limit = max_ttl_ms if max_ttl_ms is not None else settings.CONTRACT_AUTHORIZATION_MAX_TTL_MS
    if expires_at - issued_at > ttl_limit:
        raise ContractApiError(INVALID_REQUEST, f"authorization 有效期不得超过 {ttl_limit // 1000} 秒。")
    if expires_at < (current_ms if current_ms is not None else now_ms()):
        raise ContractApiError(UNAUTHORIZED, "authorization 已过期，请重新签发后重试。")


def build_signed_headers(
    secret: str,
    *,
    client_id: str,
    method: str,
    path: str,
    query: str = "",
    body: bytes = b"",
    timestamp_ms: int | None = None,
    nonce: str | None = None,
) -> dict[str, str]:
    """构造五头 + 签名（Mock GSI 验收脚本与单测共用，保证两侧算法同源）。"""
    stamp = str(timestamp_ms if timestamp_ms is not None else now_ms())
    value = nonce or str(uuid.uuid4())
    canonical = canonical_string(
        client_id=client_id,
        timestamp=stamp,
        nonce=value,
        method=method,
        target=request_target(path, query),
        body=body,
    )
    return {
        HEADER_VERSION: SIGNATURE_VERSION,
        HEADER_CLIENT_ID: client_id,
        HEADER_TIMESTAMP: stamp,
        HEADER_NONCE: value,
        HEADER_SIGNATURE: compute_signature(secret, canonical),
    }
