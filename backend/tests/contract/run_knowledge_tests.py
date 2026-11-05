"""知识写入接口（03.1 新增 / 03.2 读取 / 03.3 修改 / 03.4 删除）契约测试脚本。

```bash
# 全量执行
cd backend && .venv\\Scripts\\python.exe -m tests.contract.run_knowledge_tests

# 只跑指定用例：位置参数按「用例标签 / 请求文件名」子串匹配，未命中的用例只打印跳过
cd backend && .venv\\Scripts\\python.exe -m tests.contract.run_knowledge_tests 01_create
```

入参一律来自 `00-document/06-.../test-data/requests/`：
- 文件里的 `"content_hash": "__COMPUTED_AT_RUNTIME__"` 由 `client.resolve_content_hash()`
  调 `/v1/test/content-hash` 按 `chunks` 实算后替换（摘要口径只有服务端一份），
  避免改了切片却忘记改摘要；
- 实测结果按同名规则回写 `test-data/responses/{请求文件名}`：一个请求文件对应一个响应文件（含编号）。
"""

import json
import sys

from tests.contract import case_data, client
from tests.contract.client import call_api

sys.stdout.reconfigure(encoding="utf-8")

RESULTS: dict[str, dict] = {}

# 用例过滤器：省略时全量执行；给了就只跑命中的用例（未跑到的 responses 文件保持原样）
ONLY: str = sys.argv[1].strip() if len(sys.argv) > 1 else ""

ENTRIES_PATH = "/v1/knowledge/entries"


def section(title: str) -> None:
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def save(response_file: str, data: dict) -> None:
    """按同名请求文件名收集：responses/ 与 requests/ 一一对应（含编号）。"""
    RESULTS[response_file] = data


def build_body(file_name: str) -> tuple[dict, bytes]:
    """读请求文件 → 替换摘要占位符 → 序列化为将要发出的字节。"""
    payload = client.resolve_content_hash(case_data.load_request(file_name))
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return payload, body


def run_case(case_label: str, method: str, path: str, file_name: str, purpose: str | None = None) -> dict:
    """执行一个用例：打印入参、发出请求、打印出参，按同名请求文件名记录结果。"""
    if ONLY and ONLY not in case_label and ONLY not in file_name:
        print(f"[跳过] {case_label}（用例标签与 {file_name} 均未匹配 ONLY={ONLY}）")
        return {}
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
#  正向流程：03.1 新增 → 03.2 读取 → 03.3 修改 → 03.2 再读 → 03.4 删除 → 03.2 确认
# ══════════════════════════════════════════════════════════════════════

section("用例 #1：03.1 新增已批准切片集合 (POST /v1/knowledge/entries)")
run_case("01_create", "POST", ENTRIES_PATH, "03_knowledge_entries_create.json")

section("用例 #2：03.2 读取知识修订基线 (POST /v1/knowledge/entries/{publication_id}/query)")
run_case("02_read_v1", "POST", f"{ENTRIES_PATH}/pub-test-crud-001/query", "03_knowledge_entries_query_v1.json")

section("用例 #3：03.3 修改已批准切片集合 (PUT /v1/knowledge/entries/{publication_id})")
run_case("03_revise", "PUT", f"{ENTRIES_PATH}/pub-test-crud-001", "03_knowledge_entries_revise.json")

section("用例 #4：03.2 读取修订后基线 (POST /v1/knowledge/entries/{publication_id}/query)")
run_case("04_read_v2", "POST", f"{ENTRIES_PATH}/pub-test-crud-001/query", "03_knowledge_entries_query_v2.json")

section("用例 #5：03.4 删除已批准切片集合 (DELETE /v1/knowledge/entries/{publication_id})")
run_case("05_delete", "DELETE", f"{ENTRIES_PATH}/pub-test-crud-001", "03_knowledge_entries_delete.json")

section("用例 #6：03.2 读取已删除快照（预期 404）")
run_case("06_read_deleted", "POST", f"{ENTRIES_PATH}/pub-test-crud-001/query", "03_knowledge_entries_query_deleted.json")


# ══════════════════════════════════════════════════════════════════════
#  异常场景
# ══════════════════════════════════════════════════════════════════════

section("用例 #7：03.1 重复创建同 publication_id（FAQ 形态，预期 409 STALE_REVISION）")
run_case("07_dup_prepare", "POST", ENTRIES_PATH, "03_knowledge_entries_create_dup_1.json", purpose="铺场：先建立 FAQ 基线，供本次重复创建复现 409")
run_case("07_duplicate_create", "POST", ENTRIES_PATH, "03_knowledge_entries_create_dup_2.json")

section("用例 #8：03.1 content_hash 与切片不一致（预期 409 STALE_REVISION）")
run_case("08_bad_content_hash", "POST", ENTRIES_PATH, "03_knowledge_entries_create_bad_hash.json")

section("用例 #9：03.3 base_record_id 与当前基线不一致（预期 409 STALE_REVISION）")
run_case("09_publish_prepare", "POST", ENTRIES_PATH, "03_knowledge_entries_create_conflict.json", purpose="铺场：先建立文档基线，供本次错误基线修订复现 409")
run_case("09_bad_base_record", "PUT", f"{ENTRIES_PATH}/pub-test-conflict-001", "03_knowledge_entries_revise_bad_base.json")

section("用例 #10：03.2 查询不存在的 publication_id（预期 404 NOT_FOUND）")
run_case("10_not_found", "POST", f"{ENTRIES_PATH}/pub-nonexistent-999/query", "03_knowledge_entries_query_not_found.json")

section("用例 #11：03.1 必填字段缺失（预期 400 INVALID_REQUEST）")
run_case("11_missing_fields", "POST", ENTRIES_PATH, "03_knowledge_entries_create_incomplete.json")

section("用例 #12：03.1 chunks.seq 不连续（FAQ 形态，预期 400 INVALID_REQUEST）")
run_case("12_bad_seq", "POST", ENTRIES_PATH, "03_knowledge_entries_create_bad_seq.json")

section("用例 #13：03.4 删除不存在的 publication_id（预期 404 NOT_FOUND）")
run_case("13_delete_not_found", "DELETE", f"{ENTRIES_PATH}/pub-nonexistent-999", "03_knowledge_entries_delete_not_found.json")

section("用例 #14：03.1 resource_type 非法值（FAQ 形态，预期 400 INVALID_REQUEST）")
run_case("14_bad_resource_type", "POST", ENTRIES_PATH, "03_knowledge_entries_create_bad_type.json")


# ══════════════════════════════════════════════════════════════════════
#  保存结果
# ══════════════════════════════════════════════════════════════════════

section("测试执行完成！回写结果（responses/ 与 requests/ 同名一一对应）...")
for response_file, payload in RESULTS.items():
    case_data.save_result(response_file, payload)
    print(f"  {response_file}")

section("测试结果汇总")
for file_name, data in RESULTS.items():
    body = data.get("response_body", {})
    error_code = body.get("error", {}).get("code", "-")
    success = body.get("success", body.get("status", "-"))
    print(f"  {file_name:48} HTTP {data.get('response_status')} | error_code={error_code} | success/status={success}")
