"""`/v1` 依赖：四头验签 + nonce 防重放 + 三类幂等预约（T1.1 / T1.3）。

为什么验签要放在依赖里、而不是在函数体第一行：
1. 签名原文最后一行是**原始正文**的 SHA-256，任何「先解析再序列化」都会改变字节
   （键顺序、空格、中文转义），所以必须在 Pydantic 之前拿到 `request.body()`；
2. 路由级 `dependencies=[Depends(verified_signature)]` 保证未通过验签的请求连 400 参数校验
   都不会触发 —— 契约里签名失败只回 401 `UNAUTHORIZED`，不泄露字段级信息；
3. 五个签名头在这里声明成 Header 参数，**只为让 Swagger/OpenAPI 显示「必须带哪几个头」**；
   真实性校验仍由 `verify_signature` 做。它们一律声明为**可选**：一旦声明成必填，
   FastAPI 会先抛参数校验错误（400 `INVALID_REQUEST`），把契约规定的 401 改掉。

留痕顺序（T1.4）：每完成一步就把已知信息写进 `request.state`，**失败点之前的进度不会丢**；
被拒请求由路由类的审计内层照常落 `sbp_ai_system_api_log`，`security_check` 里能看出是
缺头、时钟越窗、签名不符还是 nonce 重放。
"""

import asyncio
from collections.abc import AsyncGenerator
from typing import Annotated, Any

from fastapi import Header, Request
from fastapi.responses import JSONResponse

from app.config.setting import settings
from app.core.logger import logger
from app.core.request_body import get_json_body
from app.core.router_class import set_audit_security, set_audit_username
from app.core.security_signature import (
    HEADER_CLIENT_ID,
    HEADER_NONCE,
    HEADER_SIGNATURE,
    HEADER_TIMESTAMP,
    HEADER_VERSION,
    SIGNATURE_VERSION,
    body_digest,
    headers_present,
    now_ms,
    signature_rule_doc,
)

from .audit import (
    AUTH_MODE_HMAC,
    NONCE_FIRST_USE,
    NONCE_REUSED,
    SIGNATURE_FAILED,
    SIGNATURE_PASSED,
    SecurityRecord,
    timestamp_deviation_ms,
)
from .errors import AUTHORIZATION_UNAVAILABLE, INVALID_REQUEST, UNAUTHORIZED, ContractApiError
from .idempotency import (
    SCOPE_EVENT,
    SCOPE_ID_KEYS,
    SCOPE_JOB,
    SCOPE_REQUEST,
    Reservation,
    complete,
    extract_biz_id,
    payload_digest,
    purge_expired as purge_idempotency,
    release,
    reserve,
)
from .nonce import nonce_store
from .signature import SignatureMaterial, verify_signature
from ..core.rich_print import pretty_log

# 签名原文口径写在 `X-AI-Signature` 的说明里：Swagger 上看不到模块 docstring，
# 而签名最常被问的就是「这个 hex 怎么算」。文字本身与 POC 共用一份（`security_signature`），
# 只有失败码的大小写口径不同。
_SIGNATURE_RULE = signature_rule_doc(unauthorized_code="UNAUTHORIZED")


def load_json_body(body: bytes) -> Any:
    """把原始字节解析成 JSON（解析失败返回 None）；只给手上已有字节的调用方用。

    依赖/路由里请用 :func:`app.core.request_body.get_json_body`——它会缓存解析结果，
    同一次请求多处读取只反序列化一次。
    """
    from app.core.request_body import parse_json_bytes

    return parse_json_bytes(body)


def _body_meta(payload: Any) -> tuple[str | None, str | None]:
    """从已解析的正文取 (request_id, user_id)：用于审计回带与操作人回填，取不到不影响主流程。"""
    if not isinstance(payload, dict):
        return None, None
    request_id = payload.get("request_id")
    user_id = payload.get("user_id")
    return (
        str(request_id) if request_id not in (None, "") else None,
        str(user_id) if user_id not in (None, "") else None,
    )


async def verified_signature(
    request: Request,
    _version: Annotated[
        str | None,
        Header(alias=HEADER_VERSION, description=f"签名版本，当前固定 `{SIGNATURE_VERSION}`。", examples=[SIGNATURE_VERSION]),
    ] = None,
    _client_id: Annotated[
        str | None,
        Header(
            alias=HEADER_CLIENT_ID,
            description="调用方标识，双方接入时约定；本期为 `gsi-service`。",
            examples=[settings.CONTRACT_V1_CLIENT_ID],
        ),
    ] = None,
    _timestamp: Annotated[
        str | None,
        Header(
            alias=HEADER_TIMESTAMP,
            description="13 位 Unix 毫秒时间戳；与服务端时钟偏差不得超过 ±5 分钟。",
            examples=["1793258848000"],
        ),
    ] = None,
    _nonce: Annotated[
        str | None,
        Header(
            alias=HEADER_NONCE,
            description="16–128 位 `[A-Za-z0-9_-]` 随机串，窗口内不可重复；重复视为重放。",
            examples=["5f1c9b2e7a4d4c2e"],
        ),
    ] = None,
    _signature: Annotated[
        str | None,
        Header(
            alias=HEADER_SIGNATURE,
            description=_SIGNATURE_RULE,
            examples=["9d1c0f4a7b2e6c8d5a3f1e9b7c4d2a0f8e6c4b2a1d9f7e5c3b1a9f7e5c3b1a9f"],
        ),
    ] = None,
) -> SignatureMaterial:
    """校验四头签名与 nonce 防重放，返回验签材料（审计存摘要，不存原文与密钥）。

    上面的 Header 参数只用于 OpenAPI 展示，函数体统一从 `request.headers` 读原始值，
    避免「声明为可选」把缺失头的场景放过。
    """
    # 签名原文最后一行是正文的 SHA-256，必须拿原始字节；body() 自带字节缓存，
    # 与下面的 get_json_body（解析结果缓存）共用同一次读取
    body = await request.body()
    request_id, user_id = _body_meta(await get_json_body(request))
    current_ms = now_ms()
    record = SecurityRecord(
        headers_present=list(headers_present(request.headers)),
        timestamp_deviation_ms=timestamp_deviation_ms(request.headers, header_name=HEADER_TIMESTAMP, current_ms=current_ms),
    )
    header_client = (request.headers.get(HEADER_CLIENT_ID) or "").strip() or None

    def flush() -> None:
        """把当前已知的鉴权上下文写入 request.state（审计在响应后异步取用）。"""
        set_audit_security(
            request,
            auth_mode=AUTH_MODE_HMAC,
            client_id=header_client,
            request_id=request_id,
            security_check=record.as_dict(),
        )

    flush()

    if not settings.CONTRACT_V1_SHARED_SECRET.strip():
        # 缺密钥时绝不能放行：放行等于把 HMAC 鉴权关掉，且不会有任何异常提示
        record.failure_code = AUTHORIZATION_UNAVAILABLE
        flush()
        raise ContractApiError(AUTHORIZATION_UNAVAILABLE, "服务端未配置 HMAC 共享密钥，暂不可用。")

    try:
        material = verify_signature(
            request.headers,
            method=request.method,
            path=request.url.path,
            query=request.url.query or "",
            body=body,
            current_ms=current_ms,
        )
    except ContractApiError as exc:
        record.signature_result = SIGNATURE_FAILED
        record.failure_code = exc.code
        flush()
        if settings.DEBUG:
            # 联调取证：签名对不上只能靠「原始字节」定位——客户端拿同一摘要自查发的是不是同一份数据
            # （多行 JSON 被工具重排、末尾换行、BOM 都会变摘要）。只打摘要与长度，不落密钥与正文。
            logger.warning(
                "验签失败 {}：path={} query='{}' body_len={} body_sha256={} timestamp={} nonce={} deviation_ms={}",
                exc.code,
                request.url.path,
                request.url.query or "",
                len(body),
                body_digest(body),
                request.headers.get(HEADER_TIMESTAMP),
                request.headers.get(HEADER_NONCE),
                record.timestamp_deviation_ms,
            )
            logger.warning(f"请求体原文: \n{pretty_log(body)}")
        raise

    record.signature_result = SIGNATURE_PASSED
    record.signature_digest = material.signature_digest
    record.timestamp_deviation_ms = material.timestamp_deviation_ms
    flush()

    try:
        first_use = await nonce_store.register(
            client_id=material.client_id,
            nonce=material.nonce,
            request_id=request_id,
            current_ms=current_ms,
        )
    except ContractApiError as exc:
        record.failure_code = exc.code
        flush()
        raise

    if not first_use:
        record.nonce_state = NONCE_REUSED
        record.failure_code = UNAUTHORIZED
        flush()
        raise ContractApiError(UNAUTHORIZED, "nonce 已使用（疑似重放请求），请重新生成。", request_id=request_id)

    record.nonce_state = NONCE_FIRST_USE
    flush()
    # 操作人：能拿到 user_id 就记用户，否则记调用方（/api/v1 侧是登录名，两套口径同一列）
    set_audit_username(request, f"user:{user_id}" if user_id else f"client:{material.client_id}")
    return material


def _idempotent_guard(scope: str):
    """按幂等域生成依赖：预约 → 交给路由 → 成功登记结果 / 失败释放。

    失败有**两条出口**，都必须释放预约，否则该 ID 会被锁在 `processing`（重试全部 409）：

    1. **正常退出**：契约路由类把异常换成了错误响应（异常不再上抛），依赖栈看到的是
       "正常返回"——FastAPI 的依赖收尾在响应发出之后才执行，try/except 根本收不到，
       所以失败判定用 `request.state.contract_failed` 标记（见 else 分支）；
    2. **异常被抛进生成器**：客户端断连（GeneratorExit）、请求任务被取消（CancelledError）、
       或路由类没接住的异常（如响应序列化失败）——此时 yield 之后的代码不会执行，
       只有 except 分支能释放（见 except 分支）。
    """

    async def dependency(request: Request) -> AsyncGenerator[Reservation, None]:
        payload = await get_json_body(request)
        biz_id = extract_biz_id(scope, payload)
        if not biz_id:
            raise ContractApiError(
                INVALID_REQUEST,
                f"缺少幂等键 {SCOPE_ID_KEYS[scope]}，写操作必须携带。",
            )

        digest = payload_digest(payload)
        reservation = await reserve(scope=scope, biz_id=biz_id, body_hash=digest)
        request.state.idempotency_reservation = reservation

        if reservation.is_replay:
            # 回放请求没有占用新预约，不能释放首次记录
            yield reservation
            return

        try:
            yield reservation
        except BaseException:
            # 出口 2：异常抛进生成器。用 shield 让释放动作在取消场景下仍能于后台完成；
            # CancelledError/GeneratorExit 单独处理（它们不是 Exception），原异常继续上抛。
            try:
                await asyncio.shield(release(scope=scope, biz_id=biz_id, body_hash=digest))
            except asyncio.CancelledError:
                logger.warning("请求取消中，幂等预约释放转入后台执行: scope={} biz_id={}", scope, biz_id)
            except Exception as exc:
                logger.error(
                    "幂等预约异常路径释放失败（该 ID 将锁在 processing 直到 TTL 过期）: scope={} biz_id={} err={}",
                    scope,
                    biz_id,
                    exc,
                )
            raise
        else:
            # 出口 1：正常退出（契约路由已把异常换成错误响应）。
            if getattr(request.state, "idempotency_settled", False):
                # 已提前登记（见 record_receipt），依赖退出只做兜底，不再重复写
                return
            result = getattr(request.state, "idempotency_result", None)
            if getattr(request.state, "contract_failed", False) or result is None:
                # 失败或骨架期无结果：释放预约
                await release(scope=scope, biz_id=biz_id, body_hash=digest)
                return
            await complete(
                scope=scope,
                biz_id=biz_id,
                body_hash=digest,
                result=result,
                tombstone=bool(getattr(request.state, "idempotency_tombstone", False)),
            )

    return dependency


async def record_receipt(
    request: Request,
    reservation: Reservation,
    *,
    status_code: int,
    body: dict[str, Any],
    tombstone: bool = False,
) -> None:
    """登记幂等回执并**立即提交**：路由成功时必须调用。

    依赖的收尾代码在响应发出后才执行，调用方收到 202 立刻重试时预约还停在 processing，
    会被误判成「原请求仍在处理中」而回 409。提前提交后，依赖退出只做兜底，不再重复登记。

    ``tombstone=True`` 用于删除类（`expires_at=NULL` 永不清理），防止「删除后重发同一
    request_id」把已删内容复活。
    """
    if reservation.is_replay:
        return
    result = {"status_code": int(status_code), "body": body}
    await complete(
        scope=reservation.scope,
        biz_id=reservation.biz_id,
        body_hash=reservation.body_hash,
        result=result,
        tombstone=tombstone,
    )
    request.state.idempotency_result = result
    request.state.idempotency_settled = True


def replay_response(reservation: Reservation) -> JSONResponse | None:
    """幂等回放：同 ID 同正文的重试直接回首次结果（状态码随结果一起存），不重复执行。"""
    if not reservation.is_replay:
        return None
    payload = reservation.result or {}
    return JSONResponse(
        status_code=int(payload.get("status_code", 200)),
        content=payload.get("body", {}),
        headers={"X-Idempotent-Replay": "true"},
    )


# 三类幂等域各一个依赖实例（scope 在生成时固定，路由用哪个域一目了然）
idempotent_job = _idempotent_guard(SCOPE_JOB)
idempotent_request = _idempotent_guard(SCOPE_REQUEST)
idempotent_event = _idempotent_guard(SCOPE_EVENT)


async def contract_store_ready() -> None:
    """启动自检：清理过期 nonce 与过期幂等台账；失败只记日志，不阻断启动。"""
    removed_nonce = await nonce_store.purge_expired()
    removed_idem = await purge_idempotency()
    if removed_nonce or removed_idem:
        logger.info("==== 契约台账清理：过期 nonce {} 条、过期幂等 {} 条 ====", removed_nonce, removed_idem)
