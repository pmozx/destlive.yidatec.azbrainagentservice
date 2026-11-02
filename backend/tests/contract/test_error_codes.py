"""契约错误码表：状态码推导、可重试口径与错误体形状。

错误码是契约对外的**唯一**错误出口，所以这里锁三件事：
码表覆盖手册用到的全部状态类别、状态码由码推导（不由调用方随手传）、
错误体形状固定且不回显内部细节。
"""

from app.modules_api.contract import errors as contract_errors


def test_error_code_table_covers_every_status_class() -> None:
    """码表覆盖契约用到的全部状态类别，且每个码都有固定话术（避免回显内部细节）。"""
    assert {400, 401, 403, 404, 409, 413, 429, 500, 503} <= set(contract_errors.ERROR_CODE_STATUS.values())
    for code in contract_errors.ERROR_CODE_STATUS:
        assert code in contract_errors.DEFAULT_MESSAGES, f"{code} 缺少固定话术"
        assert code == code.upper(), f"{code} 必须与手册一致（大写下划线）"


def test_contract_error_derives_status_and_retryable() -> None:
    """状态码与 retryable 由码推导：4xx 一律不可重试，429/503/5xx 才可重试。"""
    cases = [
        (contract_errors.INVALID_REQUEST, 400, False),
        (contract_errors.SCOPE_REQUIRED, 400, False),
        (contract_errors.UNAUTHORIZED, 401, False),
        (contract_errors.CONTEXT_OUT_OF_SCOPE, 403, False),
        (contract_errors.NOT_FOUND, 404, False),
        (contract_errors.IDEMPOTENCY_CONFLICT, 409, False),
        (contract_errors.STALE_REVISION, 409, False),
        (contract_errors.SOURCE_CHANGED, 409, False),
        (contract_errors.RESULT_EXPIRED, 409, False),
        (contract_errors.PAYLOAD_TOO_LARGE, 413, False),
        (contract_errors.RATE_LIMITED, 429, True),
        (contract_errors.AUTHORIZATION_UNAVAILABLE, 503, True),
        (contract_errors.INTERNAL_ERROR, 500, True),
    ]
    for code, status_code, retryable in cases:
        error = contract_errors.ContractApiError(code)
        assert (error.status_code, error.retryable) == (status_code, retryable), code


def test_error_body_omits_request_id_when_unknown() -> None:
    """错误体固定三段式；识别不到请求时省略 request_id（手册允许省略）。"""
    body = contract_errors.error_body(contract_errors.NOT_FOUND, "资源不存在。")
    assert body == {"error": {"code": "NOT_FOUND", "message": "资源不存在。", "retryable": False}}
    assert contract_errors.error_body("NOT_FOUND", "x", request_id="req-1")["request_id"] == "req-1"
