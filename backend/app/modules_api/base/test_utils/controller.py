"""测试辅助路由：提供签名头生成接口，方便联调与契约测试。

本模块**不经过验签依赖**，仅用于开发与测试阶段生成合法的 HMAC 签名头。
生产环境应通过 CONTRACT_TEST_ENABLE=false 关闭本路由。
"""

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.config.setting import settings
from app.core.logger import logger
from app.core.rich_print import pretty_log
from app.core.security_signature import body_digest, build_signed_headers, request_target


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
@TestRouter.get(
    "/signed-headers",
    summary="生成 HMAC 签名请求头（GET 简化版）",
    response_model=SignedHeadersResponse,
)
async def generate_signed_headers(
    request: Request,
    method: str = Query(..., description="HTTP 方法：GET / POST / PUT / DELETE"),
    path: str = Query(..., description="请求路径，如 /v1/parse/jobs"),
    query: str = Query(default="", description="查询字符串（不含 ? 前缀）"),
) -> SignedHeadersResponse:
    """为指定请求生成合法的 5 个 HMAC 签名头。

    **入参约定**：待签名的请求正文**整体作为本请求的 body 原样传入**（不再包一层 JSON 外壳），
    `method` / `path` / `query` 走查询参数。摘要直接对收到的原始字节计算，
    与最终对外发送的正文完全一致，不会因二次序列化（键序、空格、中文转义）导致签名对不上。

    **客户端标识与时效**：`client_id` 取服务端配置（`CONTRACT_V1_CLIENT_ID`），
    时间戳取当前毫秒，nonce 每次随机生成，调用方无需关心。

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

    # 请求体即待签名正文：取原始字节，避免解析后再序列化导致签名漂移
    body_bytes = await request.body()

    # 客户端标识、时间戳、nonce 均由服务端决定：配置值 / 当前毫秒 / 随机生成
    headers = build_signed_headers(
        secret,
        client_id=settings.CONTRACT_V1_CLIENT_ID,
        method=method,
        path=path,
        query=query,
        body=body_bytes,
    )

    # 后台留痕：输出与 tests/contract/debug_signed_headers.py 同一份调试信息
    logger.info(
        "测试辅助接口生成签名头：{} {}（query={}）\n"
        "== 请求体 ==\n{}\n"
        "== 请求体长度 ==\n{}\n"
        "== 请求体 SHA-256 ==\n{}\n"
        "== 自定义安全头 ==\n{}"
        "== Apifox 请求头 ==\n{}",
        method.upper(),
        path,
        query,
        body_bytes.decode("utf-8", errors="replace") or "（空）",
        len(body_bytes),
        body_digest(body_bytes),
        pretty_log(headers),
        "\n".join(f"{name}:{value}" for name, value in headers.items()),
    )

    # 构造签名原文（用于调试）
    timestamp = headers["X-AI-Timestamp"]
    resolved_nonce = headers["X-AI-Nonce"]
    signing_string = "\n".join([
        "v1",
        settings.CONTRACT_V1_CLIENT_ID,
        timestamp,
        resolved_nonce,
        method.upper(),
        request_target(path, query),
        body_digest(body_bytes),
    ])

    return SignedHeadersResponse(
        headers=headers,
        signing_string=signing_string,
        client_id=settings.CONTRACT_V1_CLIENT_ID,
        timestamp_ms=int(timestamp),
        nonce=resolved_nonce,
    )
