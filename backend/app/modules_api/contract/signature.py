"""正式 `/v1` 的验签适配层：算法在 `app/core/security_signature.py`，这里只做口径翻译。

契约口径（手册 §四「公共请求头与签名」）——签名原文 7 行、LF 连接、**末尾无换行**：

    v1
    {X-AI-Client-Id}
    {X-AI-Timestamp}
    {X-AI-Nonce}
    {大写 HTTP 方法}
    {原始路径及可选查询字符串}
    {请求正文原始字节的 SHA-256 小写十六进制摘要}

签名 = ``HMAC-SHA256(共享密钥, 原文)`` 的 64 位小写十六进制。

本模块与公共安全层的关系（POC 下线后仍成立）：
1. **算法同一份**：原文拼装、时间窗、nonce 格式、`authorization` 跨度都在
   `app/core/security_signature.py`，本模块不持有拷贝；
2. 本模块只做**口径翻译**：验签失败统一 401 `UNAUTHORIZED`，共享密钥未配置归 503
   `AUTHORIZATION_UNAVAILABLE`（失败关闭），话术不回显期望值；
3. nonce 的原子登记要落库，放在 `nonce.py`；审计留痕放在 `deps.py`；
4. 验签必须拿**原始字节**算摘要，不能先解析 JSON 再序列化（键序、空格、中文转义都会变），
   所以验签在依赖里、在 Pydantic 之前执行。
"""

from collections.abc import Mapping
from dataclasses import dataclass

from app.config.setting import settings
from app.core.security_signature import (
    AuthorizationCheckError,
    AuthorizationFailure,
    SignatureCheckError,
    SignatureFailure,
    VerifiedSignature,
    check_authorization_window,
    check_signature,
    parse_signature_headers as _parse_signature_headers,
)

from .errors import AUTHORIZATION_UNAVAILABLE, INVALID_REQUEST, SCOPE_REQUIRED, UNAUTHORIZED, ContractApiError

__all__ = [
    "SignatureMaterial",
    "parse_signature_headers",
    "verify_authorization",
    "verify_signature",
]


@dataclass(frozen=True)
class SignatureMaterial:
    """验签通过后的材料：只留客户端标识与签名摘要（审计存摘要，不存原文与密钥）。"""

    client_id: str
    nonce: str
    timestamp_ms: int
    timestamp_deviation_ms: int
    signature_digest: str
    headers_present: tuple[str, ...]


def _as_contract_error(exc: SignatureCheckError) -> ContractApiError:
    """验签失败归口：密钥未配置是「服务不可用」，其余一律 401 `UNAUTHORIZED`。"""
    if exc.reason is SignatureFailure.SECRET_NOT_CONFIGURED:
        return ContractApiError(AUTHORIZATION_UNAVAILABLE, exc.message)
    return ContractApiError(UNAUTHORIZED, exc.message)


def parse_signature_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """取五个签名头的去空白值；缺任意一个都按 401 拒绝（不是 400 参数校验）。"""
    try:
        return _parse_signature_headers(headers)
    except SignatureCheckError as exc:
        raise _as_contract_error(exc) from exc


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
    try:
        verified: VerifiedSignature = check_signature(
            headers,
            method=method,
            path=path,
            query=query,
            body=body,
            secret=secret if secret is not None else settings.CONTRACT_V1_SHARED_SECRET,
            client_id=client_id if client_id is not None else settings.CONTRACT_V1_CLIENT_ID,
            clock_skew_ms=clock_skew_ms if clock_skew_ms is not None else settings.CONTRACT_CLOCK_SKEW_MS,
            current_ms=current_ms,
        )
    except SignatureCheckError as exc:
        raise _as_contract_error(exc) from exc

    return SignatureMaterial(
        client_id=verified.client_id,
        nonce=verified.nonce,
        timestamp_ms=verified.timestamp_ms,
        timestamp_deviation_ms=verified.timestamp_deviation_ms,
        # 审计只存「前 8 + 后 4」：够对账，又不足以复用签名
        signature_digest=verified.signature_hex[:8] + verified.signature_hex[-4:],
        headers_present=verified.headers_present,
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
    判定本身在 `app/core/security_signature.check_authorization_window`，这里只做形态检查与归口。
    """
    if authorization is None:
        return
    if not isinstance(authorization, Mapping):
        raise ContractApiError(INVALID_REQUEST, "authorization 必须是对象。")

    issued_at = authorization.get("issued_at")
    expires_at = authorization.get("expires_at")
    if not isinstance(issued_at, int) or not isinstance(expires_at, int):
        raise ContractApiError(INVALID_REQUEST, "authorization.issued_at / expires_at 须为 Unix 毫秒整数。")

    ttl_limit = max_ttl_ms if max_ttl_ms is not None else settings.CONTRACT_AUTHORIZATION_MAX_TTL_MS
    try:
        check_authorization_window(
            scope_id=str(authorization.get("scope_id") or ""),
            issued_at=issued_at,
            expires_at=expires_at,
            current_ms=current_ms,
            max_ttl_ms=ttl_limit,
        )
    except AuthorizationCheckError as exc:
        raise _as_contract_authorization_error(exc) from exc


def _as_contract_authorization_error(exc: AuthorizationCheckError) -> ContractApiError:
    """`authorization` 失败归口：缺范围 400 `SCOPE_REQUIRED`，已过期 401，其余 400。"""
    if exc.reason is AuthorizationFailure.SCOPE_MISSING:
        return ContractApiError(SCOPE_REQUIRED, "缺少 authorization.scope_id。")
    if exc.reason is AuthorizationFailure.EXPIRED:
        return ContractApiError(UNAUTHORIZED, exc.message)
    return ContractApiError(INVALID_REQUEST, exc.message)
