"""安全头校验留痕（T1.4）：`security_check` JSONB 的结构化装载与回填。

契约要求（计划 §七.1、C4）：20 个 `/v1` 接口全量落 `sbp_ai_system_api_log`，
**被拒请求同样留痕**，安全头校验结果落 `security_check`，**签名只存摘要、nonce 全量留存**。

留痕结构（设计文档 §5.2）：

    {
      "headers_present": ["X-AI-Signature-Version", ...],   # 实际到达的头，缺失的一眼可见
      "timestamp_deviation_ms": 812,                         # 服务端时钟 - 请求时间戳
      "nonce_state": "first_use" | "reused" | "not_checked",  # 只有签名通过才会登记 nonce
      "signature_result": "passed" | "failed" | "skipped",
      "signature_digest": "9d1c0f4a...c3b1",                 # 签名前 8 + 后 4，不存原文
      "failure_code": "UNAUTHORIZED" | null                  # 失败原因归口到契约错误码
    }

为什么只存摘要：`X-AI-Signature` 本身不是密钥，但它是「共享密钥 + 正文 + nonce」的函数，
落原文等于给离线爆破送样本；前 8 + 后 4 足够在取证时和调用方对账，又不足以复用。
"""

from collections.abc import Mapping
from dataclasses import dataclass, field

from starlette.requests import Request

# 审计口径的固定取值（与设计文档 §5.2 一一对应，指标看板按这些值分组）
AUTH_MODE_HMAC = "hmac"
SIGNATURE_PASSED = "passed"
SIGNATURE_FAILED = "failed"
SIGNATURE_SKIPPED = "skipped"
NONCE_FIRST_USE = "first_use"
NONCE_REUSED = "reused"
NONCE_NOT_CHECKED = "not_checked"


@dataclass
class SecurityRecord:
    """一次请求的安全头校验过程记录：随失败点逐步填充，最后整体落 `security_check`。"""

    headers_present: list[str] = field(default_factory=list)
    timestamp_deviation_ms: int | None = None
    nonce_state: str = NONCE_NOT_CHECKED
    signature_result: str = SIGNATURE_SKIPPED
    signature_digest: str | None = None
    failure_code: str | None = None

    def as_dict(self) -> dict[str, object]:
        """转 JSONB 可序列化的 dict（时间偏差缺失时写 null，不省略键，便于按列统计）。"""
        return {
            "headers_present": self.headers_present,
            "timestamp_deviation_ms": self.timestamp_deviation_ms,
            "nonce_state": self.nonce_state,
            "signature_result": self.signature_result,
            "signature_digest": self.signature_digest,
            "failure_code": self.failure_code,
        }


def timestamp_deviation_ms(headers: Mapping[str, str], *, header_name: str, current_ms: int) -> int | None:
    """尽力算出时钟偏差（毫秒）：时间戳不合规时返回 None，不影响主流程。"""
    stamp = (headers.get(header_name) or "").strip()
    if not stamp.isdigit() or len(stamp) != 13:
        return None
    return current_ms - int(stamp)


def mark_failure(request: Request, code: str) -> None:
    """把最终错误码补进 `security_check.failure_code`。

    验签依赖只能记下「签名阶段」的失败；签名通过之后才失败的请求（`authorization` 超时效、
    幂等异正文、参数校验）如果不在出口补记，审计里就只看到 `signature_result=passed` 却 4xx，
    取证时分不清是「谁拒的」。只写第一次（最靠前的失败点最接近根因）。
    """
    check = getattr(request.state, "audit_security_check", None)
    if isinstance(check, dict) and not check.get("failure_code"):
        check["failure_code"] = code
