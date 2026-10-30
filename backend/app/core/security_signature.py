"""`/v1` 接口安全请求头的公共实现：HMAC 五头验签、nonce 防重放、`authorization` 时效。

契约口径（`02-1` 手册 §四「公共请求头与签名」），签名原文 7 行、LF 连接、**末尾无换行**：

    v1
    {X-AI-Client-Id}
    {X-AI-Timestamp}
    {X-AI-Nonce}
    {大写 HTTP 方法}
    {原始路径及可选查询字符串}
    {请求正文原始字节的 SHA-256 小写十六进制摘要}

签名 = ``HMAC-SHA256(共享密钥, 原文)`` 的 64 位小写十六进制。

为什么抽成公共模块（`app/poc` 与 `app/contract` 同源）：
1. **算法只有一份**：原文拼装、时间窗、nonce 格式、`authorization` 跨度这四处任一处写歪，
   联调时都会以「签名失败」的形式暴露却很难定位；两份拷贝迟早会漂移，改一处忘一处。
2. 本模块**不认识任何错误体**：失败一律抛 `SignatureCheckError` / `AuthorizationCheckError`，
   由 `app/poc/signature.py`（小写码，401/400）与 `app/contract/signature.py`（大写码，
   密钥缺失 503 失败关闭）各自翻译。算法共享，对外口径互不污染。
3. nonce 的**时序**（先验签再登记，否则伪造请求可以抢占合法 nonce）在这里固定；**存储**由
   调用方注入：POC 用进程内 `InMemoryNonceLedger`，契约层用落库的 `contract.nonce.NonceStore`
   （异步，登记在 `deps.py` 里编排，仍遵守同一时序）。
4. 验签必须拿**原始字节**算摘要，不能先解析 JSON 再序列化（键序、空格、中文转义都会变），
   所以调用点在路由依赖里、在 Pydantic 之前。
"""

import hashlib
import hmac
import re
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Protocol

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
NONCE_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9_-]{16,128}$")

# 契约：authorization 有效期最长 120 秒（调用方可整体收紧，不可放宽）
DEFAULT_AUTHORIZATION_MAX_TTL_MS: Final[int] = 120_000


# ── 纯计算（签名原文 / 摘要 / 自签头） ──────────────────────────────────


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
    """构造五头 + 签名（Mock GSI 脚本、验收脚本与单测共用，保证两侧算法同源）。"""
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


# ── 头部读取（OpenAPI / 审计共用） ──────────────────────────────────────


def headers_present(headers: Mapping[str, str]) -> tuple[str, ...]:
    """当前请求实际带了哪几个签名头（审计 `security_check.headers_present` 用）。"""
    return tuple(name for name in SIGNATURE_HEADERS if (headers.get(name) or "").strip())


def timestamp_deviation_ms(
    headers: Mapping[str, str],
    *,
    header_name: str = HEADER_TIMESTAMP,
    current_ms: int,
) -> int | None:
    """尽力算出时钟偏差（服务端 - 请求时间戳）：时间戳不合规时返回 None，不影响主流程。"""
    stamp = (headers.get(header_name) or "").strip()
    if not stamp.isdigit() or len(stamp) != 13:
        return None
    return current_ms - int(stamp)


def signature_rule_doc(*, unauthorized_code: str = "UNAUTHORIZED") -> str:
    """`X-AI-Signature` 的 OpenAPI 说明（Swagger 上看不到模块 docstring，算法只能写在这里）。

    两套接入层共用同一段文字，只有失败码的大小写口径不同（POC 小写、契约层大写）。
    """
    return f"""HMAC-SHA256(共享密钥, 签名原文) 的 64 位**小写**十六进制；比较时大小写敏感。

签名原文共 7 行，用 LF 连接、**末尾不留换行**：
1. `v1`（即 `X-AI-Signature-Version` 的值）
2. `X-AI-Client-Id` 的值
3. `X-AI-Timestamp` 的值
4. `X-AI-Nonce` 的值
5. 大写 HTTP 方法，如 `POST`
6. 原始路径及可选查询字符串，如 `/v1/knowledge/search`（不含协议与域名）
7. 请求正文原始字节的 SHA-256 小写 hex；GET 无正文时按空字节序列计算

任意一项不符、头缺失、时间戳超出 ±5 分钟窗口、nonce 重放，均返回 401 `{unauthorized_code}`，
不回显期望值。"""


# ── 验签失败的中性表示（由接入层翻译成各自的错误码） ──────────────────


class SignatureFailure(StrEnum):
    """验签失败原因：接入层按它映射错误码与 HTTP 状态，不靠匹配话术。"""

    SECRET_NOT_CONFIGURED = "secret_not_configured"
    MISSING_HEADERS = "missing_headers"
    UNSUPPORTED_VERSION = "unsupported_version"
    CLIENT_MISMATCH = "client_mismatch"
    BAD_TIMESTAMP = "bad_timestamp"
    CLOCK_SKEW = "clock_skew"
    BAD_NONCE = "bad_nonce"
    SIGNATURE_MISMATCH = "signature_mismatch"
    NONCE_REUSED = "nonce_reused"


class SignatureCheckError(Exception):
    """验签失败（与错误体无关）：`reason` 供接入层映射，`message` 为契约固定话术。"""

    def __init__(
        self,
        reason: SignatureFailure,
        message: str,
        *,
        missing_headers: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.missing_headers = missing_headers


@dataclass(frozen=True)
class VerifiedSignature:
    """验签通过后的材料：只留客户端标识与签名 hex（审计只存摘要，不存原文与密钥）。"""

    client_id: str
    nonce: str
    timestamp_ms: int
    timestamp_deviation_ms: int
    signature_hex: str
    headers_present: tuple[str, ...]


def parse_signature_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """取五个签名头的去空白值；缺任意一个都按 401 拒绝（不是 400 参数校验）。"""
    values = {name: (headers.get(name) or "").strip() for name in SIGNATURE_HEADERS}
    missing = tuple(name for name, value in values.items() if not value)
    if missing:
        raise SignatureCheckError(
            SignatureFailure.MISSING_HEADERS,
            f"缺少签名请求头：{'、'.join(missing)}。",
            missing_headers=missing,
        )
    return values


def check_signature(
    headers: Mapping[str, str],
    *,
    method: str,
    path: str,
    query: str,
    body: bytes,
    secret: str,
    client_id: str,
    clock_skew_ms: int,
    current_ms: int | None = None,
) -> VerifiedSignature:
    """按契约顺序验签：密钥 → 缺头 → 版本/客户端 → 时间窗 → nonce 格式 → 签名比对。

    **不含 nonce 防重放**：那一步要落台账（进程内或数据库），见 `verify_request_signature`
    与 `contract.nonce.NonceStore`。先验签再登记，否则伪造请求可以抢占合法 nonce。
    """
    if not secret:
        # 缺密钥时绝不能放行：放行等于把 HMAC 鉴权关掉，且不会有任何异常提示
        raise SignatureCheckError(SignatureFailure.SECRET_NOT_CONFIGURED, "服务端未配置 HMAC 共享密钥，暂不可用。")

    stamp_now = current_ms if current_ms is not None else now_ms()
    values = parse_signature_headers(headers)

    version = values[HEADER_VERSION]
    header_client = values[HEADER_CLIENT_ID]
    timestamp = values[HEADER_TIMESTAMP]
    nonce = values[HEADER_NONCE]
    signature = values[HEADER_SIGNATURE]

    if version != SIGNATURE_VERSION:
        raise SignatureCheckError(
            SignatureFailure.UNSUPPORTED_VERSION,
            f"不支持的签名版本：{version}（当前仅 {SIGNATURE_VERSION}）。",
        )
    if header_client != client_id:
        raise SignatureCheckError(SignatureFailure.CLIENT_MISMATCH, "客户端标识无效。")
    if not timestamp.isdigit() or len(timestamp) != 13:
        raise SignatureCheckError(SignatureFailure.BAD_TIMESTAMP, "时间戳须为 13 位 Unix 毫秒。")

    stamp_ms = int(timestamp)
    deviation_ms = stamp_now - stamp_ms
    if abs(deviation_ms) > clock_skew_ms:
        raise SignatureCheckError(
            SignatureFailure.CLOCK_SKEW,
            "请求时间戳超出允许窗口，请校准时钟后重新签名。",
        )
    if not NONCE_PATTERN.match(nonce):
        raise SignatureCheckError(
            SignatureFailure.BAD_NONCE,
            "nonce 须为 16–128 位字母、数字、下划线或连字符。",
        )

    canonical = canonical_string(
        client_id=header_client,
        timestamp=timestamp,
        nonce=nonce,
        method=method,
        target=request_target(path, query),
        body=body,
    )
    # 固定时间比较，避免按字节提前返回泄漏「前几位对上了」
    if not hmac.compare_digest(compute_signature(secret, canonical), signature):
        raise SignatureCheckError(
            SignatureFailure.SIGNATURE_MISMATCH,
            "服务签名认证失败，请检查签名、时间戳和 nonce。",
        )

    return VerifiedSignature(
        client_id=header_client,
        nonce=nonce,
        timestamp_ms=stamp_ms,
        timestamp_deviation_ms=deviation_ms,
        signature_hex=signature,
        headers_present=headers_present(headers),
    )


# ── nonce 防重放：时序共享，存储可替换 ────────────────────────────────


class NonceLedger(Protocol):
    """nonce 首用登记（同步口径）：首次到达返回 True，窗口内重复返回 False。"""

    def register(self, client_id: str, nonce: str, *, expires_at_ms: int, current_ms: int) -> bool: ...


class InMemoryNonceLedger:
    """进程内 nonce 台账：单实例够用。

    局限（契约层已用落库的 `NonceStore` 替代）：多副本各存一份会漏判重放；重启即清零，
    窗口内还能再放一次。因此只服务 POC 冒烟，不作为正式实现。
    """

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


def verify_request_signature(
    headers: Mapping[str, str],
    *,
    method: str,
    path: str,
    query: str,
    body: bytes,
    secret: str,
    client_id: str,
    clock_skew_ms: int,
    current_ms: int | None = None,
    ledger: NonceLedger | None = None,
) -> VerifiedSignature:
    """验签 + （可选）同步 nonce 登记：一次调用完成「验签 → 登记」的正确时序。

    契约层的台账是异步落库，自己在 `deps.py` 里按同样顺序编排（验签通过后才登记），
    因此不复用本函数；POC 直接用它，少一处手写顺序出错的机会。
    """
    stamp_now = current_ms if current_ms is not None else now_ms()
    verified = check_signature(
        headers,
        method=method,
        path=path,
        query=query,
        body=body,
        secret=secret,
        client_id=client_id,
        clock_skew_ms=clock_skew_ms,
        current_ms=stamp_now,
    )
    if ledger is not None:
        first_use = ledger.register(
            verified.client_id,
            verified.nonce,
            expires_at_ms=verified.timestamp_ms + clock_skew_ms,
            current_ms=stamp_now,
        )
        if not first_use:
            raise SignatureCheckError(
                SignatureFailure.NONCE_REUSED,
                "nonce 已使用（疑似重放请求），请重新生成。",
            )
    return verified


# ── authorization 时效（请求正文里的短期授权块） ──────────────────────


class AuthorizationFailure(StrEnum):
    """`authorization` 校验失败原因：接入层据此决定 400 还是 401。"""

    SCOPE_MISSING = "scope_missing"
    INVALID_RANGE = "invalid_range"
    TTL_EXCEEDED = "ttl_exceeded"
    EXPIRED = "expired"


class AuthorizationCheckError(Exception):
    """`authorization` 不合法：`ttl_limit_ms` 供接入层组织话术（缺省时读 `message`）。"""

    def __init__(
        self,
        reason: AuthorizationFailure,
        message: str,
        *,
        ttl_limit_ms: int | None = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.ttl_limit_ms = ttl_limit_ms


def check_authorization_window(
    *,
    scope_id: str,
    issued_at: int,
    expires_at: int,
    current_ms: int | None = None,
    max_ttl_ms: int = DEFAULT_AUTHORIZATION_MAX_TTL_MS,
) -> None:
    """校验 `authorization{scope_id,issued_at,expires_at}`：跨度 ≤ 上限、且未过期。

    契约只写「最长 120 秒，过期拒绝」，没给专用错误码（POC 疑点 Q1/Q2）：
    跨度超限是**请求本身**的问题（400 语义），已过期是**凭据失效**（401 语义），
    由接入层各自归口，本函数只负责判定。
    """
    if not scope_id.strip():
        raise AuthorizationCheckError(AuthorizationFailure.SCOPE_MISSING, "缺少 authorization.scope_id。")
    if expires_at <= issued_at:
        raise AuthorizationCheckError(
            AuthorizationFailure.INVALID_RANGE,
            "authorization.expires_at 必须大于 issued_at。",
        )
    if expires_at - issued_at > max_ttl_ms:
        raise AuthorizationCheckError(
            AuthorizationFailure.TTL_EXCEEDED,
            f"authorization 有效期不得超过 {max_ttl_ms // 1000} 秒。",
            ttl_limit_ms=max_ttl_ms,
        )
    stamp_now = current_ms if current_ms is not None else now_ms()
    if expires_at < stamp_now:
        raise AuthorizationCheckError(
            AuthorizationFailure.EXPIRED,
            "authorization 已过期，请重新签发后重试。",
        )
