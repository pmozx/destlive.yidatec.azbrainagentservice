"""契约接口调用客户端：签名头与整包摘要都交给服务端测试辅助接口算，脚本只负责组包、发送与断言。

为什么不在脚本里自己算：
1. 签名原文的拼接口径（大写方法、path+query、body 摘要）属于服务端契约，测试脚手架再算一遍
   就会出现「两份实现、各自都对」的假绿灯；
2. `POST|GET /v1/test/signed-headers` 直接把请求体原始字节当作待签名正文，
   这里只要把「同一个 bytes」既交给它签名、又原样发出去，就不会出现缩进/换行导致的签名漂移；
3. `POST /v1/test/content-hash` 用服务端那份 `canonical_content_hash` 算摘要，
   测试侧不再自带一份口径。

前置条件：服务已启动，且 `env/.env.dev` 中 `CONTRACT_TEST_ENABLE=true`、`CONTRACT_V1_SHARED_SECRET` 有值。
"""

import time
from typing import Any

import httpx

BASE_URL = "http://127.0.0.1:8002"

# 知识接口的整包摘要由运行期按 chunks 实算，JSON 里只写这个占位符
CONTENT_HASH_PLACEHOLDER = "__COMPUTED_AT_RUNTIME__"


def signed_headers(method: str, path: str, body: bytes = b"", query: str = "") -> dict[str, str]:
    """调用测试辅助接口换回一组签名头；body 传什么，服务端就对什么算摘要。"""
    params = {"method": method, "path": path}
    if query:
        params["query"] = query
    resp = httpx.post(
        f"{BASE_URL}/v1/test/signed-headers",
        params=params,
        content=body,
        headers={"Content-Type": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["headers"]


def compute_content_hash(payload: dict[str, Any]) -> str:
    """调测试辅助接口算知识整包摘要：口径只有服务端一份（canonical_content_hash）。"""
    resp = httpx.post(
        f"{BASE_URL}/v1/test/content-hash",
        json={"chunks": payload["chunks"]},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["content_hash"]


def resolve_content_hash(payload: dict[str, Any]) -> dict[str, Any]:
    """把 `__COMPUTED_AT_RUNTIME__` 换成实算摘要（已写死的摘要原样保留）。"""
    if payload.get("content_hash") != CONTENT_HASH_PLACEHOLDER:
        return payload
    return {**payload, "content_hash": compute_content_hash(payload)}


def call_api(method: str, path: str, body: bytes = b"", query: str = "", timeout: float = 120.0) -> dict[str, Any]:
    """按契约发一次请求，返回 {status_code, headers, body}，不做任何断言。"""
    url = f"{BASE_URL}{path}" + (f"?{query}" if query else "")
    headers = signed_headers(method, path, body, query)
    headers["Content-Type"] = "application/json"
    resp = httpx.request(method, url, content=body, headers=headers, timeout=timeout)
    return {"status_code": resp.status_code, "headers": dict(resp.headers), "body": resp.json()}


def wait_parse_job(job_id: str, timeout: int = 600, interval: int = 5) -> dict[str, Any]:
    """轮询 #02 直到 succeeded / failed；非 200 立即失败（404 再等也等不出结果）。"""
    last: dict[str, Any] | None = None
    deadline = time.time() + timeout
    while time.time() < deadline:
        last = call_api("GET", f"/v1/parse/jobs/{job_id}")
        print(f"  [{job_id}] 状态={last['body'].get('status')} 进度={last['body'].get('progress')}%")
        if last["status_code"] != 200:
            # 404 多为命中幂等回放（台账在、任务行被清），见 04-实现与运维说明 §5.1
            raise SystemExit(f"[{job_id}] #02 返回 HTTP {last['status_code']}，停止轮询：{last['body']}")
        status = last["body"].get("status")
        if status in ("succeeded", "failed"):
            return last
        time.sleep(interval)
    return last or {"status_code": None, "body": {"status": "timeout"}}
