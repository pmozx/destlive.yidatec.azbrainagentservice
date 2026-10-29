"""HMAC 四头签名与防重放（POC 最小实现）。

契约口径（手册「公共请求头与签名」）：

签名原文 7 行、LF 连接、**末尾无换行**：:

    v1
    {X-AI-Client-Id}
    {X-AI-Timestamp}
    {X-AI-Nonce}
    {大写 HTTP 方法}
    {原始路径及可选查询字符串}
    {请求正文原始字节的 SHA-256 小写十六进制摘要}

签名 = ``HMAC-SHA256(共享密钥, 原文)`` 的 64 位小写十六进制。

POC 的取舍（T1.1 生产化时替换）：
1. nonce 只存进程内字典 —— 单实例 POC 够用，多实例/重启期共享要靠持久表（T1.1）；
2. 验签必须拿**原始字节**算摘要，不能先解析 JSON 再序列化：所以验签放在依赖里、在 Pydantic 之前；
3. 比较用 ``hmac.compare_digest``，任何失败都只回 ``unauthorized`` + 固定话术，不回显期望值。
"""

import hashlib
import hmac
import re
import time
import uuid
from dataclasses import dataclass
from typing import Final, Mapping

from app.config.setting import settings

from .errors import UNAUTHORIZED, PocApiError

SIGNATURE_VERSION = "v1"
HEADER_VERSION = "X-AI-Signature-Version"
HEADER_CLIENT_ID = "X-AI-Client-Id"
HEADER_TIMESTAMP = "X-AI-Timestamp"
HEADER_NONCE = "X-AI-Nonce"
HEADER_SIGNATURE = "X-AI-Signature"

# 五个签名头的规范清单：OpenAPI 声明与断言都以它为准（增删头时只改一处）
SIGNATURE_HEADERS: Final[tuple[str, ...]] = (
    HEADER_VERSION,
    HEADER_CLIENT_ID,
    HEADER_TIMESTAMP,
    HEADER_NONCE,
    HEADER_SIGNATURE,
)

# 契约：16–128 位字母、数字、下划线或连字符
_NONCE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
# 契约：authorization 有效期最长 120 秒
AUTHORIZATION_MAX_TTL_MS = 120_000


def now_ms() -> int:
    """当前 Unix 毫秒（统一入口，便于测试注入）。"""
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
    return "\n".join(
        [
            SIGNATURE_VERSION,
            client_id,
            timestamp,
            nonce,
            method.upper(),
            target,
            body_digest(body),
        ]
    )


def compute_signature(secret: str, canonical: str) -> str:
    """HMAC-SHA256 输出 64 位小写十六进制。"""
    return hmac.new(secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()


def request_target(path: str, query: str) -> str:
    """签名原文第 6 行：原始路径 + 可选查询字符串（不含 scheme/host）。"""
    return f"{path}?{query}" if query else path


@dataclass(frozen=True)
class SignatureContext:
    """验签结果：只留 client_id 与签名摘要（审计用摘要，不存原文与密钥）。"""

    client_id: str
    nonce: str
    timestamp_ms: int
    signature_digest: str


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
    """构造四头 + 签名（Mock GSI 脚本与单测共用，保证两侧算法同源）。"""
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


class NonceRegistry:
    """nonce 原子登记（POC 用进程内字典）：同 nonce 二次到达即判定重放。"""

    def __init__(self) -> None:
        self._seen: dict[tuple[str, str], int] = {}

    def register(self, client_id: str, nonce: str, *, expires_at_ms: int, current_ms: int) -> bool:
        """登记成功返回 True；已存在（重放）返回 False。顺带清掉过期条目，避免字典无限增长。"""
        self._sweep(current_ms)
        key = (client_id, nonce)
        if key in self._seen:
            return False
        self._seen[key] = expires_at_ms
        return True

    def clear(self) -> None:
        """清空登记（单测与演示脚本复位用）。"""
        self._seen.clear()

    def size(self) -> int:
        return len(self._seen)

    def _sweep(self, current_ms: int) -> None:
        expired = [key for key, expire_at in self._seen.items() if expire_at < current_ms]
        for key in expired:
            self._seen.pop(key, None)


# 进程级单例：POC 单实例假设，见模块头部第 1 条取舍
nonce_registry = NonceRegistry()


def verify_signed_request(
    headers: Mapping[str, str],
    *,
    method: str,
    path: str,
    query: str,
    body: bytes,
    current_ms: int | None = None,
    secret: str | None = None,
    client_id: str | None = None,
    clock_skew_ms: int | None = None,
    registry: NonceRegistry | None = None,
) -> SignatureContext:
    """按契约顺序验签：缺头 → 版本/客户端 → 时间窗 → 签名 → nonce 防重放。"""
    expected_client_id = client_id if client_id is not None else settings.POC_V1_CLIENT_ID
    shared_secret = secret if secret is not None else settings.POC_V1_SHARED_SECRET
    skew = clock_skew_ms if clock_skew_ms is not None else settings.POC_V1_CLOCK_SKEW_MS
    store = registry if registry is not None else nonce_registry
    stamp_now = current_ms if current_ms is not None else now_ms()

    version = (headers.get(HEADER_VERSION) or "").strip()
    header_client = (headers.get(HEADER_CLIENT_ID) or "").strip()
    timestamp = (headers.get(HEADER_TIMESTAMP) or "").strip()
    nonce = (headers.get(HEADER_NONCE) or "").strip()
    signature = (headers.get(HEADER_SIGNATURE) or "").strip()

    if not all((version, header_client, timestamp, nonce, signature)):
        raise PocApiError(UNAUTHORIZED, "缺少签名请求头，需同时提供版本、客户端、时间戳、nonce 与签名。", status_code=401)
    if version != SIGNATURE_VERSION:
        raise PocApiError(UNAUTHORIZED, f"不支持的签名版本：{version}（当前仅 {SIGNATURE_VERSION}）。", status_code=401)
    if header_client != expected_client_id:
        raise PocApiError(UNAUTHORIZED, "客户端标识无效。", status_code=401)

    if not timestamp.isdigit() or len(timestamp) != 13:
        raise PocApiError(UNAUTHORIZED, "时间戳须为 13 位 Unix 毫秒。", status_code=401)
    stamp_ms = int(timestamp)
    if abs(stamp_now - stamp_ms) > skew:
        raise PocApiError(UNAUTHORIZED, "请求时间戳超出允许窗口，请校准时钟后重新签名。", status_code=401)

    if not _NONCE_PATTERN.match(nonce):
        raise PocApiError(UNAUTHORIZED, "nonce 须为 16–128 位字母、数字、下划线或连字符。", status_code=401)

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
        raise PocApiError(UNAUTHORIZED, "服务签名认证失败，请检查签名、时间戳和 nonce。", status_code=401)

    # 签名通过后才登记 nonce：否则伪造请求可以抢占合法 nonce
    if not store.register(header_client, nonce, expires_at_ms=stamp_ms + skew, current_ms=stamp_now):
        raise PocApiError(UNAUTHORIZED, "nonce 已使用（疑似重放请求），请重新生成。", status_code=401)

    return SignatureContext(
        client_id=header_client,
        nonce=nonce,
        timestamp_ms=stamp_ms,
        signature_digest=body_digest(signature.encode("utf-8")),
    )


def verify_authorization_window(
    *,
    scope_id: str,
    issued_at: int,
    expires_at: int,
    current_ms: int | None = None,
) -> None:
    """校验 `authorization{scope_id,issued_at,expires_at}`：跨度 ≤120s、且未过期。

    过期用 401（凭据失效语义）；跨度超限用 400（参数非法）。契约未定义这两处的专用业务码，
    已在 T0.4 契约疑点清单登记。
    """
    stamp_now = current_ms if current_ms is not None else now_ms()
    if not scope_id.strip():
        raise PocApiError("invalid_request", "authorization.scope_id 不能为空。", status_code=400)
    if expires_at <= issued_at:
        raise PocApiError("invalid_request", "authorization.expires_at 必须大于 issued_at。", status_code=400)
    if expires_at - issued_at > AUTHORIZATION_MAX_TTL_MS:
        raise PocApiError("invalid_request", "authorization 有效期不得超过 120 秒。", status_code=400)
    if expires_at < stamp_now:
        raise PocApiError(UNAUTHORIZED, "authorization 已过期，请重新签发后重试。", status_code=401)
