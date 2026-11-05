"""检索接口（#07 `POST /v1/knowledge/search`）契约测试脚本。

```bash
cd backend && .venv\\Scripts\\python.exe -m tests.contract.run_search_tests
```

入参一律来自 `00-document/06-.../test-data/requests/`：
- `"content_hash": "__COMPUTED_AT_RUNTIME__"` 由 `client.resolve_content_hash()` 调
  `/v1/test/content-hash` 按 `chunks` 实算（摘要口径只有服务端一份）；
- `authorization` 的 `issued_at` / `expires_at` 写占位符，由 `case_data.resolve_authorization()`
  在发送前换算成当前时刻的毫秒值（静态值一落地就过期，无法区分「跨度超限」与「已过期」）；
- 实测结果按同名规则回写 `test-data/responses/{请求文件名}`：一个请求文件对应一个响应文件（含编号）。

前置数据：`04_knowledge_search_prepare_entry.json` 通过 03.1 把论文切片写入 GSI 发布单元
`0876a2b0-2788-4816-a1fd-7f26a2bfd061`（team=AUD_SMOKE，成员 user_id=1）。检索用例都以
「该团队成员的签名 user_id」调用，才能验证授权下推后的召回。

铺场可重复执行：首次写库回 201；再次执行时 request_id 与正文都没变，幂等台账按原响应回放
201。改动铺场正文（切片、publication_id 等）必须同步换一个新的 request_id，否则会命中
`409 IDEMPOTENCY_CONFLICT`（同 ID 不同正文），后续检索用例将因无数据而全部 0 命中。
"""

import json
import sys

from tests.contract import case_data, client
from tests.contract.client import call_api

sys.stdout.reconfigure(encoding="utf-8")

RESULTS: dict[str, dict] = {}

SEARCH_PATH = "/v1/knowledge/search"
ENTRIES_PATH = "/v1/knowledge/entries"

#: GSI 发布单元 ID（sbp_km_doc_publication.id，team=AUD_SMOKE）；契约里 publication_id 就是这个值
PUBLICATION_ID = "0876a2b0-2788-4816-a1fd-7f26a2bfd061"


def section(title: str) -> None:
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def save(response_file: str, data: dict) -> None:
    """按同名请求文件名收集：responses/ 与 requests/ 一一对应（含编号）。"""
    RESULTS[response_file] = data


def build_body(file_name: str) -> tuple[dict, bytes]:
    """读请求文件 → 替换摘要与授权时效占位符 → 序列化为将要发出的字节。"""
    payload = case_data.load_request(file_name)
    payload = client.resolve_content_hash(payload)
    payload = case_data.resolve_authorization(payload)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return payload, body


def run_case(case_label: str, method: str, path: str, file_name: str, purpose: str | None = None) -> dict:
    """执行一个用例：打印入参、发出请求、打印出参，按同名请求文件名记录结果。"""
    payload, body = build_body(file_name)
    print(f"\n[请求] {case_label} | {method} {path}（数据：{file_name}）")
    print("\n[请求入参]")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    result = call_api(method, path, body)
    print(f"\n[响应] HTTP {result['status_code']}")
    print(json.dumps(result["body"], indent=2, ensure_ascii=False))
    record = {
        "request_file": file_name,
        "request_path": f"{method} {path}",
        "request": payload,
        "response_status": result["status_code"],
        "response_body": result["body"],
    }
    if purpose:
        record["purpose"] = purpose
    save(file_name, record)
    return result


# ══════════════════════════════════════════════════════════════════════
#  铺场：把切片写进 GSI 发布单元（检索只看已入库且已授权的生效快照）
# ══════════════════════════════════════════════════════════════════════

section("铺场：03.1 新增已批准切片集合（POST /v1/knowledge/entries，publication_id = GSI 发布单元）")
run_case(
    "00_prepare_entry",
    "POST",
    ENTRIES_PATH,
    "04_knowledge_search_prepare_entry.json",
    purpose="铺场：把论文三条切片写入 GSI 发布单元，供后续检索用例召回；重复执行按幂等台账回放 201",
)


# ══════════════════════════════════════════════════════════════════════
#  正向流程：命中 / 条数受 limit 约束 / 无证据 / 授权与租户隔离
# ══════════════════════════════════════════════════════════════════════

section("用例 #1：关键词检索命中（预期 200，hits 非空、no_evidence=false）")
run_case("01_keyword_hit", "POST", SEARCH_PATH, "04_knowledge_search_keyword.json")

section("用例 #2：命中条数受 limit 约束（预期 200，returned_count=1）")
run_case("02_keyword_limit", "POST", SEARCH_PATH, "04_knowledge_search_keyword_limit.json")

section("用例 #3：库外关键词无证据（预期 200，hits=[]、no_evidence=true）")
run_case("03_no_evidence", "POST", SEARCH_PATH, "04_knowledge_search_no_evidence.json")

section("用例 #4：非本受众团队成员检索（预期 200，越权 0 命中）")
run_case("04_out_of_scope_user", "POST", SEARCH_PATH, "04_knowledge_search_out_of_scope_user.json")

section("用例 #5：用户无任何受众团队（预期 200，0 命中、不报 500）")
run_case("05_no_team_user", "POST", SEARCH_PATH, "04_knowledge_search_no_team_user.json")

section("用例 #6：user_id 非数字主键（预期 200，按无权限处理为 0 命中）")
run_case("06_non_numeric_user", "POST", SEARCH_PATH, "04_knowledge_search_non_numeric_user.json")

section("用例 #7：租户隔离（tenant_id=gsi-other，预期 200、0 命中）")
run_case("07_other_tenant", "POST", SEARCH_PATH, "04_knowledge_search_other_tenant.json")


# ══════════════════════════════════════════════════════════════════════
#  自然语言检索（向量召回 + RRF + rerank）/ 异常场景：授权时效 / 参数校验
# ══════════════════════════════════════════════════════════════════════

section("用例 #8：自然语言检索（预期 200；语义召回论文切片，返回 hits 即 no_evidence=false）")
run_case(
    "08_natural_language",
    "POST",
    SEARCH_PATH,
    "04_knowledge_search_natural_language.json",
    purpose=(
        "自然语言模式走向量通道 + RRF 融合 + rerank：query「造林对流量历时曲线有什么影响」与铺场切片"
        "无字面重叠，仍应命中；向量化不可用时为 500 INTERNAL_ERROR(retryable=true)，"
        "授权不可用时为 503 AUTHORIZATION_UNAVAILABLE——都不是 no_evidence"
    ),
)

section("用例 #9：authorization 跨度 300 秒（预期 400 INVALID_REQUEST）")
run_case("09_authorization_too_long", "POST", SEARCH_PATH, "04_knowledge_search_authorization_too_long.json")

section("用例 #10：authorization 已过期（预期 401 UNAUTHORIZED）")
run_case("10_authorization_expired", "POST", SEARCH_PATH, "04_knowledge_search_authorization_expired.json")

section("用例 #11：缺少 authorization 块（预期 400）")
run_case("11_authorization_missing", "POST", SEARCH_PATH, "04_knowledge_search_authorization_missing.json")

section("用例 #12：query 只有空白（预期 400 INVALID_REQUEST）")
run_case("12_empty_query", "POST", SEARCH_PATH, "04_knowledge_search_empty_query.json")

section("用例 #13：limit 超出 1–100（预期 400 INVALID_REQUEST）")
run_case("13_limit_out_of_range", "POST", SEARCH_PATH, "04_knowledge_search_limit_out_of_range.json")

section("用例 #14：mode 非枚举值（预期 400 INVALID_REQUEST）")
run_case("14_invalid_mode", "POST", SEARCH_PATH, "04_knowledge_search_invalid_mode.json")


# ══════════════════════════════════════════════════════════════════════
#  保存结果
# ══════════════════════════════════════════════════════════════════════

section("测试执行完成！回写结果（responses/ 与 requests/ 同名一一对应）...")
for response_file, payload in RESULTS.items():
    case_data.save_result(response_file, payload)
    print(f"  {response_file}")


def consistency(data: dict) -> str:
    """#07 自洽断言：returned_count == len(hits)，no_evidence == (hits 为空)。"""
    body = data.get("response_body", {})
    if data.get("response_status") != 200 or "hits" not in body:
        return "-"
    hits = body.get("hits") or []
    ok = body.get("returned_count") == len(hits) and body.get("no_evidence") == (not hits)
    return "ok" if ok else "FAIL"


section("测试结果汇总")
for file_name, data in RESULTS.items():
    body = data.get("response_body", {})
    error_code = body.get("error", {}).get("code", "-")
    hits = body.get("hits")
    detail = f"hits={len(hits)}" if isinstance(hits, list) else f"error_code={error_code}"
    print(
        f"  {file_name:52} HTTP {data.get('response_status')} | {detail:28}"
        f" | consistency={consistency(data)}"
    )
