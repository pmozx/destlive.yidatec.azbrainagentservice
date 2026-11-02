"""M1 里程碑验收：契约接入层「骨架成立」的证据。

**为什么单独放 ``stages/``**：下面这些断言验的是「这一阶段的交付是否成立」，而不是某个
模块的长期行为。它们会随阶段推进**被替换而不是被修修补补**——

- 20 条路由清单：骨架期要逐条对上手册；等 #07/#14 真正实现后，清单应改为「与手册一致」
  而不是「恰好 20 条」；
- 501 骨架占位：占位实现被真实链路取代后，这条必须删掉，否则会挡住正常返回；
- 错误响应声明：骨架期统一指向 `ContractErrorResponse`，将来分接口细化后同样要改。

放进 ``modules/`` 或 ``contract/`` 会让它们和不该过期的东西混在一起，最终没人敢删。
**约定**：一个子包 = 一个里程碑（``m1_*/``、``m2_*/`` …），阶段收尾时整包复核一次。
"""

from app import create_app
from app.contract import errors as contract_errors
from app.contract.signature import build_signed_headers
from app.config.setting import settings

from tests.support import BODY, call, requires_db

#: 手册 §五「接口总览」的 20 条：路径 / 方法锁定，任何增删改都必须显式反映到这里
EXPECTED_ROUTES: set[tuple[str, str]] = {
    ("POST", "/v1/parse/jobs"),
    ("GET", "/v1/parse/jobs/{job_id}"),
    ("POST", "/v1/knowledge/entries"),
    ("POST", "/v1/knowledge/entries/{publication_id}/query"),
    ("PUT", "/v1/knowledge/entries/{publication_id}"),
    ("DELETE", "/v1/knowledge/entries/{publication_id}"),
    ("POST", "/v1/knowledge/search"),
    ("POST", "/v1/chat/sessions"),
    ("POST", "/v1/chat/sessions/query"),
    ("POST", "/v1/chat/sessions/{session_id}/query"),
    ("PATCH", "/v1/chat/sessions/{session_id}"),
    ("DELETE", "/v1/chat/sessions/{session_id}"),
    ("DELETE", "/v1/chat/sessions/{session_id}/messages"),
    ("POST", "/v1/chat/completions"),
    ("POST", "/v1/chat/requests/{target_request_id}/query"),
    ("POST", "/v1/knowledge/duplicates/check"),
    ("POST", "/v1/knowledge/duplicates/groups/query"),
    ("POST", "/v1/knowledge/duplicates/groups/{group_id}/query"),
    ("POST", "/v1/knowledge/duplicates/merge-preview"),
    ("POST", "/v1/knowledge/entries/{publication_id}/deactivate"),
}


def test_twenty_contract_routes_are_registered() -> None:
    """20 条路由的路径与方法与手册 §五 完全一致（POC 已被契约层接管，不再重复挂载）。"""
    schema = create_app().openapi()
    actual = {
        (method.upper(), path)
        for path, operations in schema["paths"].items()
        if path.startswith("/v1/")
        for method in operations
    }
    assert actual == EXPECTED_ROUTES
    assert len(actual) == 20


def test_error_responses_point_to_contract_error_body() -> None:
    """400/401/422/501 的响应都指向契约错误体，422 的说明要交代「已归一成 400」。"""
    operation = create_app().openapi()["paths"]["/v1/knowledge/search"]["post"]
    responses = operation["responses"]
    assert {"400", "401", "403", "404", "409", "413", "429", "501", "503"} <= set(responses)
    for code in ("400", "401", "409", "501"):
        ref = responses[code]["content"]["application/json"]["schema"]["$ref"]
        assert "ContractErrorResponse" in ref, code
    assert "400" in responses["422"]["description"]


@requires_db
async def test_signed_skeleton_returns_501_placeholder() -> None:
    """带签名打通骨架路由：#07 检索尚未实现，返回 501 占位且错误体与 request_id 回带齐全。

    这条就是"骨架成立"的定义：验签、幂等、审计、错误出口全部到位，业务逻辑还没写。
    #07 上线后本用例应删除（而不是改成断言 200）。
    """
    headers = build_signed_headers(
        settings.CONTRACT_V1_SHARED_SECRET,
        client_id=settings.CONTRACT_V1_CLIENT_ID,
        method="POST",
        path="/v1/knowledge/search",
        body=BODY,
    )
    response = await call("POST", "/v1/knowledge/search", body=BODY, headers=headers)
    assert response.status_code == 501
    body = response.json()
    assert body["error"]["code"] == contract_errors.INTERNAL_ERROR
    assert body["error"]["retryable"] is False
    assert body["request_id"] == "req-001"
