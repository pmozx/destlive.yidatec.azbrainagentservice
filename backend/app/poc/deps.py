"""`/v1` 依赖：验签（原始正文）与 POC 请求级数据库会话。

为什么验签要放在依赖里、而不是在函数体第一行：
1. 签名原文最后一行是**原始正文**的 SHA-256，任何「先解析再序列化」都会改变字节
   （键顺序、空格、中文转义），所以必须在 Pydantic 之前拿到 `request.body()`；
2. 路由级 `dependencies=[Depends(verified_signature)]` 保证未通过验签的请求连 400 参数校验
   都不会触发 —— 契约里签名失败只回 401 `unauthorized`，不泄露字段级信息。
3. 五个签名头在这里同时声明成 Header 参数，**只为让 Swagger/OpenAPI 显示「必须带哪几个头」**；
   真实性校验仍由 `verify_signed_request` 做。它们一律声明为**可选**：一旦声明成必填，
   FastAPI 会先抛参数校验错误（400 `invalid_request`），把契约规定的 401 `unauthorized` 改掉。
"""

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.setting import settings
from app.core.database import async_session_factory

from .signature import (
    HEADER_CLIENT_ID,
    HEADER_NONCE,
    HEADER_SIGNATURE,
    HEADER_TIMESTAMP,
    HEADER_VERSION,
    SIGNATURE_VERSION,
    SignatureContext,
    verify_signed_request,
)
from .store import require_poc_store

# 签名原文口径写在 `X-AI-Signature` 的说明里：Swagger 上看不到模块 docstring，
# 而签名最常被问的就是「这个 hex 怎么算」。
_SIGNATURE_RULE = """HMAC-SHA256(共享密钥, 签名原文) 的 64 位**小写**十六进制；比较时大小写敏感。

签名原文共 7 行，用 LF 连接、**末尾不留换行**：
1. `v1`（即 `X-AI-Signature-Version` 的值）
2. `X-AI-Client-Id` 的值
3. `X-AI-Timestamp` 的值
4. `X-AI-Nonce` 的值
5. 大写 HTTP 方法，如 `POST`
6. 原始路径及可选查询字符串，如 `/v1/knowledge/search`（不含协议与域名）
7. 请求正文原始字节的 SHA-256 小写 hex；GET 无正文时按空字节序列计算

任意一项不符、头缺失、时间戳超出 ±5 分钟窗口、nonce 重放，均返回 401 `unauthorized`，
不回显期望值。"""


async def verified_signature(
    request: Request,
    _version: Annotated[
        str | None,
        Header(alias=HEADER_VERSION, description=f"签名版本，当前固定 `{SIGNATURE_VERSION}`。", examples=[SIGNATURE_VERSION]),
    ] = None,
    _client_id: Annotated[
        str | None,
        Header(alias=HEADER_CLIENT_ID, description="调用方标识，双方接入时约定；POC 为 `gsi-service`。", examples=[settings.POC_V1_CLIENT_ID]),
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
        Header(alias=HEADER_SIGNATURE, description=_SIGNATURE_RULE, examples=["9d1c0f4a7b2e6c8d5a3f1e9b7c4d2a0f8e6c4b2a1d9f7e5c3b1a9f7e5c3b1a9f"]),
    ] = None,
) -> SignatureContext:
    """校验四头签名与 nonce 防重放，返回验签上下文（POC 只用于日志，不落库）。

    上面的 Header 参数只用于 OpenAPI 展示，函数体统一从 `request.headers` 读原始值，
    避免「声明为可选」把缺失头的场景放过。
    """
    body = await request.body()
    return verify_signed_request(
        request.headers,
        method=request.method,
        path=request.url.path,
        query=request.url.query or "",
        body=body,
    )


async def poc_db(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """POC 请求级会话：一个请求一个事务，但**提交/回滚由这里显式决定**。

    为什么不直接用 `session.begin()` 上下文管理器：FastAPI 的依赖退出代码在**响应发出之后**
    才执行。错误体已被 `PocApiRoute` 转成错误响应（异常不再上抛），上下文管理器会当成功提交，
    把失败请求的半成品数据写进库。所以用 `request.state.poc_failed` 标记 + 显式回滚。

    先确认存储可用再开会话：POC 依赖 JSONB + pgvector，非 PostgreSQL 环境直接 503，
    避免跑到一半才发现表建不出来。
    """
    require_poc_store()
    async with async_session_factory() as session:
        await session.begin()
        try:
            yield session
            if getattr(request.state, "poc_failed", False):
                await session.rollback()
            else:
                await session.commit()
        except Exception:
            await session.rollback()
            raise
