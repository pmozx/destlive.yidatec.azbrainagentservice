"""`/v1` 的 OpenAPI 口径：签名头与错误体必须在 /docs 上看得见。

为什么值得单独测：POC 的接口文档是交付给 GSI 联调看的，签名头一旦从 OpenAPI 里消失，
对方只能靠翻聊天记录猜 header 名；而把签名头误声明成必填，会让「缺头」从 401 变成 400，
直接把契约的鉴权语义改掉 —— 这两件事都不会有异常抛出来，只能靠断言守。
"""

from app import create_app
from app.poc.signature import SIGNATURE_HEADERS

# 任选一个操作即可：签名依赖挂在 router 上，所有 /v1 操作的参数集应当一致
OPERATION = ("/v1/knowledge/search", "post")


def _operation() -> dict:
    schema = create_app().openapi()
    path, method = OPERATION
    return schema["paths"][path][method]


def test_signature_headers_are_documented_as_optional() -> None:
    """四个签名头都出现在 OpenAPI 参数里：位于 header、非必填、且带说明。

    误标必填会让 FastAPI 先抛参数校验错误，缺头就从契约的 401 变成 400；
    没有说明时，Swagger 上联调方也看不出该填什么。
    """
    parameters = {item["name"]: item for item in _operation().get("parameters", [])}

    for header in SIGNATURE_HEADERS:
        assert header in parameters, f"OpenAPI 缺少签名头 {header}"
        assert parameters[header]["in"] == "header"
        assert parameters[header]["required"] is False, (
            f"{header} 不能声明为必填：FastAPI 会先抛参数校验错误，缺头就变成 400 而不是契约要求的 401"
        )
        assert parameters[header]["description"], f"{header} 缺少说明，Swagger 上看不出该填什么"


def test_signature_parameter_explains_the_canonical_string() -> None:
    """X-AI-Signature 的说明里必须写清算法、行数、换行符与摘要口径。"""
    parameters = {item["name"]: item for item in _operation().get("parameters", [])}
    description = parameters["X-AI-Signature"]["description"]

    # 签名原文 7 行口诀，少写一行都会让联调方算不出正确的 hex
    for fragment in ("HMAC-SHA256", "7 行", "LF", "SHA-256"):
        assert fragment in description


def test_error_body_is_documented() -> None:
    """400/401 的响应指向契约错误体，且 422 的说明要交代「会被归一成 400」。"""
    responses = _operation()["responses"]
    assert {"400", "401"} <= set(responses), "签名失败与参数失败的错误体必须出现在响应说明里"
    for code in ("400", "401", "422"):
        schema = responses[code]["content"]["application/json"]["schema"]
        assert "error" in schema["$ref"].lower() or "error" in schema.get("properties", {}), (
            f"{code} 的响应必须指向契约错误体，否则 Swagger 上看不到错误字段"
        )
    # FastAPI 默认会写 422，但 PocApiRoute 把参数校验失败归一成 400；描述必须说明这点
    assert "400" in responses["422"]["description"]
