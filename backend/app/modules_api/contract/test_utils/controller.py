"""测试辅助路由：提供签名头生成接口，方便联调与契约测试。

本模块**不经过验签依赖**，仅用于开发与测试阶段生成合法的 HMAC 签名头。
生产环境应通过 CONTRACT_TEST_ENABLE=false 关闭本路由。
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.config.setting import settings
from app.core.security_signature import build_signed_headers


class SignedHeadersRequest(BaseModel):
    """签名头生成请求参数。"""

    method: str = Field(description="HTTP 方法：GET / POST / PUT / DELETE")
    path: str = Field(description="请求路径，如 /v1/parse/jobs")
    query: str = Field(default="", description="查询字符串（不含 ? 前缀）")
    body: str = Field(default="", description="请求正文（JSON 字符串）")
    client_id: str = Field(default=None, description="客户端 ID，默认使用配置值")
    timestamp_ms: int = Field(default=None, description="时间戳（毫秒），默认当前时间")
    nonce: str = Field(default=None, description="Nonce，默认自动生成")


class SignedHeadersResponse(BaseModel):
    """签名头生成响应。"""

    headers: dict[str, str] = Field(description="5 个签名请求头")
    signing_string: str = Field(description="签名原文（7 行，LF 连接）")
    client_id: str = Field(description="使用的客户端 ID")
    timestamp_ms: int = Field(description="使用的时间戳")
    nonce: str = Field(description="使用的 nonce")


TestRouter = APIRouter(
    prefix="/test",
    tags=["测试辅助"],
)


@TestRouter.post(
    "/signed-headers",
    summary="生成 HMAC 签名请求头",
    response_model=SignedHeadersResponse,
)
async def generate_signed_headers(payload: SignedHeadersRequest) -> SignedHeadersResponse:
    """为指定请求生成合法的 5 个 HMAC 签名头。

    **用途**：联调与契约测试时，快速生成可用的签名头，无需手工计算。

    **注意**：
    - 本接口不需要签名验证（鸡生蛋问题）
    - 生产环境应通过 CONTRACT_TEST_ENABLE=false 关闭
    """
    if not settings.CONTRACT_TEST_ENABLE:
        raise HTTPException(status_code=404, detail="测试接口已禁用")

    secret = settings.CONTRACT_V1_SHARED_SECRET
    if not secret:
        raise HTTPException(status_code=503, detail="服务端未配置 HMAC 共享密钥")

    client_id = payload.client_id or settings.CONTRACT_V1_CLIENT_ID
    body_bytes = payload.body.encode("utf-8") if payload.body else b""

    # 生成签名头
    headers = build_signed_headers(
        secret,
        client_id=client_id,
        method=payload.method,
        path=payload.path,
        query=payload.query,
        body=body_bytes,
        timestamp_ms=payload.timestamp_ms,
        nonce=payload.nonce,
    )

    # 构造签名原文（用于调试）
    from app.core.security_signature import body_digest, request_target

    timestamp = headers["X-AI-Timestamp"]
    nonce = headers["X-AI-Nonce"]
    signing_string = "\n".join([
        "v1",
        client_id,
        timestamp,
        nonce,
        payload.method.upper(),
        request_target(payload.path, payload.query),
        body_digest(body_bytes),
    ])

    return SignedHeadersResponse(
        headers=headers,
        signing_string=signing_string,
        client_id=client_id,
        timestamp_ms=int(timestamp),
        nonce=nonce,
    )


@TestRouter.get(
    "/signed-headers",
    summary="生成 HMAC 签名请求头（GET 简化版）",
    response_model=SignedHeadersResponse,
)
async def generate_signed_headers_get(
    method: str = "GET",
    path: str = "/v1/parse/jobs",
    query: str = "",
    client_id: str = None,
) -> SignedHeadersResponse:
    """GET 方式的简化签名头生成，适用于无正文的请求。"""
    payload = SignedHeadersRequest(
        method=method,
        path=path,
        query=query,
        body="",
        client_id=client_id,
    )
    return await generate_signed_headers(payload)
