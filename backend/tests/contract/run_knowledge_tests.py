"""知识写入接口（03.1-03.4）契约测试脚本：CRUD 全链路 + 异常场景。"""

import hashlib
import httpx
import json
import sys
import os

sys.stdout.reconfigure(encoding='utf-8')

BASE_URL = "http://127.0.0.1:8002"
RESULTS = {}

# ── 工具函数 ──────────────────────────────────────────────────────────

def get_signed_headers(method: str, path: str, body: str = "") -> dict:
    resp = httpx.post(f"{BASE_URL}/v1/test/signed-headers", json={
        "method": method, "path": path, "body": body,
    })
    resp.raise_for_status()
    return resp.json()["headers"]


def call_api(method: str, path: str, payload: dict | None = None, timeout: float = 120.0) -> dict:
    body_str = json.dumps(payload, ensure_ascii=False) if payload else ""
    headers = get_signed_headers(method, path, body_str)
    headers["Content-Type"] = "application/json"
    resp = httpx.request(method, f"{BASE_URL}{path}", content=body_str, headers=headers, timeout=timeout)
    return {"status_code": resp.status_code, "body": resp.json()}


def compute_content_hash(chunks: list[dict]) -> str:
    hash_fields = ("seq", "heading", "content", "page_from", "page_to")
    rows = [{f: chunk.get(f) for f in hash_fields} for chunk in chunks]
    rows.sort(key=lambda r: r["seq"])
    serialized = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def save(name, data):
    RESULTS[name] = data


def section(title: str):
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


# ── 测试数据构造 ──────────────────────────────────────────────────────

PUB_ID = "pub-test-crud-001"
REC_V1 = "rec-test-crud-001-v1"
REC_V2 = "rec-test-crud-001-v2"


def make_chunks_v1():
    return [
        {"seq": 1, "heading": "研究目的", "content": "本研究旨在开发一种评估造林对流量持续时间曲线（FDC）影响的方法。", "page_from": 1, "page_to": 1},
        {"seq": 2, "heading": "研究方法", "content": "使用来自澳大利亚、南非和新西兰的10个流域的每日流量数据进行分析。", "page_from": 2, "page_to": 3},
        {"seq": 3, "heading": "主要结论", "content": "所开发的方法能有效去除降雨变异性，为理解造林对流量持续时间曲线的影响提供了洞察。", "page_from": 10, "page_to": 11},
    ]


def make_chunks_v2():
    return [
        {"seq": 1, "heading": "研究目的", "content": "本研究旨在开发一种评估造林对流量持续时间曲线（FDC）影响的方法，以去除降雨变异性对流量变化的影响。", "page_from": 1, "page_to": 1},
        {"seq": 2, "heading": "研究方法", "content": "使用来自澳大利亚、南非和新西兰的10个流域的每日流量数据进行分析。", "page_from": 2, "page_to": 3},
        {"seq": 3, "heading": "模型公式", "content": "Q_% = a + b(ΔP) + Y/(1 + exp((T - T_half)/S))，其中各参数含义详见原文。", "page_from": 4, "page_to": 5},
        {"seq": 4, "heading": "主要结论", "content": "所开发的方法能有效去除降雨变异性，为理解造林对流量持续时间曲线的影响提供了洞察。", "page_from": 10, "page_to": 11},
    ]


def make_create_payload(**overrides) -> dict:
    chunks = make_chunks_v1()
    base = {
        "request_id": "req-kb-create-001",
        "tenant_id": "gsi-default",
        "user_id": "user-demo-001",
        "resource_type": "document",
        "resource_id": "doc-demo-10001",
        "publication_id": PUB_ID,
        "record_id": REC_V1,
        "version_id": "v1",
        "revision": 1,
        "knowledge_no": "KB-2026-001",
        "content_hash": compute_content_hash(chunks),
        "metadata": {"title": "造林对流量历时曲线的影响", "doc_type": "document", "owner": "kali03", "language": "zh"},
        "chunks": chunks,
    }
    base.update(overrides)
    return base


def make_revise_payload(**overrides) -> dict:
    chunks = make_chunks_v2()
    base = {
        "request_id": "req-kb-revise-001",
        "tenant_id": "gsi-default",
        "user_id": "user-demo-001",
        "resource_type": "document",
        "resource_id": "doc-demo-10001",
        "publication_id": PUB_ID,
        "record_id": REC_V2,
        "version_id": "v1",
        "revision": 2,
        "knowledge_no": "KB-2026-001",
        "content_hash": compute_content_hash(chunks),
        "metadata": {"title": "造林对流量历时曲线的影响（修订版）", "doc_type": "document", "owner": "kali03", "language": "zh"},
        "chunks": chunks,
        "base_record_id": REC_V1,
    }
    base.update(overrides)
    return base


def make_delete_payload(**overrides) -> dict:
    base = {
        "request_id": "req-kb-delete-001",
        "tenant_id": "gsi-default",
        "user_id": "user-demo-001",
        "resource_type": "document",
        "resource_id": "doc-demo-10001",
        "publication_id": PUB_ID,
        "record_id": REC_V2,
        "version_id": "v1",
        "revision": 3,
        "reason": "测试删除：知识内容已过时，不再适用。",
    }
    base.update(overrides)
    return base


def make_query_payload(record_id: str, request_id: str = "req-kb-query-001") -> dict:
    return {
        "request_id": request_id,
        "tenant_id": "gsi-default",
        "user_id": "user-demo-001",
        "record_id": record_id,
        "limit": 100,
    }


# ══════════════════════════════════════════════════════════════════════
#  正向流程：03.1 新增 → 03.2 读取 → 03.3 修改 → 03.2 再读 → 03.4 删除 → 03.2 确认
# ══════════════════════════════════════════════════════════════════════

# 用例 #1：03.1 新增
section("用例 #1：03.1 新增已批准切片集合 (POST /v1/knowledge/entries)")
create_payload = make_create_payload()
print("\n[请求入参]")
print(json.dumps(create_payload, indent=2, ensure_ascii=False))
r1 = call_api("POST", "/v1/knowledge/entries", create_payload)
print(f"\n[响应] HTTP {r1['status_code']}")
print(json.dumps(r1["body"], indent=2, ensure_ascii=False))
save("01_create", {"request": create_payload, "response_status": r1["status_code"], "response_body": r1["body"]})


# 用例 #2：03.2 读取基线
section("用例 #2：03.2 读取知识修订基线 (POST /v1/knowledge/entries/{pub_id}/query)")
query_v1 = make_query_payload(record_id=REC_V1)
print("\n[请求入参]")
print(json.dumps(query_v1, indent=2, ensure_ascii=False))
r2 = call_api("POST", f"/v1/knowledge/entries/{PUB_ID}/query", query_v1)
print(f"\n[响应] HTTP {r2['status_code']}")
print(json.dumps(r2["body"], indent=2, ensure_ascii=False))
save("02_read_v1", {"request": query_v1, "response_status": r2["status_code"], "response_body": r2["body"]})


# 用例 #3：03.3 修改
section("用例 #3：03.3 修改已批准切片集合 (PUT /v1/knowledge/entries/{pub_id})")
revise_payload = make_revise_payload()
print("\n[请求入参]")
print(json.dumps(revise_payload, indent=2, ensure_ascii=False))
r3 = call_api("PUT", f"/v1/knowledge/entries/{PUB_ID}", revise_payload)
print(f"\n[响应] HTTP {r3['status_code']}")
print(json.dumps(r3["body"], indent=2, ensure_ascii=False))
save("03_revise", {"request": revise_payload, "response_status": r3["status_code"], "response_body": r3["body"]})


# 用例 #4：03.2 读取修订后基线
section("用例 #4：03.2 读取修订后基线 (POST /v1/knowledge/entries/{pub_id}/query)")
query_v2 = make_query_payload(record_id=REC_V2, request_id="req-kb-query-v2")
print("\n[请求入参]")
print(json.dumps(query_v2, indent=2, ensure_ascii=False))
r4 = call_api("POST", f"/v1/knowledge/entries/{PUB_ID}/query", query_v2)
print(f"\n[响应] HTTP {r4['status_code']}")
print(json.dumps(r4["body"], indent=2, ensure_ascii=False))
save("04_read_v2", {"request": query_v2, "response_status": r4["status_code"], "response_body": r4["body"]})


# 用例 #5：03.4 删除
section("用例 #5：03.4 删除已批准切片集合 (DELETE /v1/knowledge/entries/{pub_id})")
delete_payload = make_delete_payload()
print("\n[请求入参]")
print(json.dumps(delete_payload, indent=2, ensure_ascii=False))
r5 = call_api("DELETE", f"/v1/knowledge/entries/{PUB_ID}", delete_payload)
print(f"\n[响应] HTTP {r5['status_code']}")
print(json.dumps(r5["body"], indent=2, ensure_ascii=False))
save("05_delete", {"request": delete_payload, "response_status": r5["status_code"], "response_body": r5["body"]})


# 用例 #6：03.2 读取已删除的快照（应 404）
section("用例 #6：03.2 读取已删除快照（预期 404）")
query_deleted = make_query_payload(record_id=REC_V2, request_id="req-kb-query-deleted")
print("\n[请求入参]")
print(json.dumps(query_deleted, indent=2, ensure_ascii=False))
r6 = call_api("POST", f"/v1/knowledge/entries/{PUB_ID}/query", query_deleted)
print(f"\n[响应] HTTP {r6['status_code']}")
print(json.dumps(r6["body"], indent=2, ensure_ascii=False))
save("06_read_deleted", {"request": query_deleted, "response_status": r6["status_code"], "response_body": r6["body"]})


# ══════════════════════════════════════════════════════════════════════
#  异常场景
# ══════════════════════════════════════════════════════════════════════

# 用例 #7：03.1 重复创建（同 publication_id → 409）
section("用例 #7：03.1 重复创建同 publication_id（预期 409 STALE_REVISION）")
dup_pub_id = "pub-test-dup-001"
dup_create = make_create_payload(
    publication_id=dup_pub_id, record_id="rec-dup-v1", revision=1, request_id="req-kb-dup-1"
)
# 先创建一次
call_api("POST", "/v1/knowledge/entries", dup_create)
# 再用同一个 publication_id 创建
dup_create_2 = make_create_payload(
    publication_id=dup_pub_id, record_id="rec-dup-v2", revision=1, request_id="req-kb-dup-2"
)
print("\n[请求入参（第二次创建，同 publication_id）]")
print(json.dumps(dup_create_2, indent=2, ensure_ascii=False))
r7 = call_api("POST", "/v1/knowledge/entries", dup_create_2)
print(f"\n[响应] HTTP {r7['status_code']}")
print(json.dumps(r7["body"], indent=2, ensure_ascii=False))
save("07_duplicate_create", {"request": dup_create_2, "response_status": r7["status_code"], "response_body": r7["body"]})


# 用例 #8：03.1 content_hash 不匹配（→ 409 STALE_REVISION）
section("用例 #8：03.1 content_hash 与切片不一致（预期 409 STALE_REVISION）")
bad_hash_payload = make_create_payload(
    publication_id="pub-test-badhash-001",
    record_id="rec-badhash-v1",
    revision=1,
    request_id="req-kb-badhash",
)
bad_hash_payload["content_hash"] = "sha256:0000000000000000000000000000000000000000000000000000000000000000"
print("\n[请求入参（content_hash 故意篡改）]")
print(json.dumps(bad_hash_payload, indent=2, ensure_ascii=False))
r8 = call_api("POST", "/v1/knowledge/entries", bad_hash_payload)
print(f"\n[响应] HTTP {r8['status_code']}")
print(json.dumps(r8["body"], indent=2, ensure_ascii=False))
save("08_bad_content_hash", {"request": bad_hash_payload, "response_status": r8["status_code"], "response_body": r8["body"]})


# 用例 #9：03.3 base_record_id 不匹配（→ 409）
section("用例 #9：03.3 base_record_id 与当前基线不一致（预期 409 STALE_REVISION）")
# 先创建一个用于测试修订冲突的 publication
conflict_pub = "pub-test-conflict-001"
conflict_create = make_create_payload(
    publication_id=conflict_pub, record_id="rec-conflict-v1", revision=1, request_id="req-conflict-create"
)
call_api("POST", "/v1/knowledge/entries", conflict_create)
# 用错误的 base_record_id 修订
bad_revise = make_revise_payload(
    publication_id=conflict_pub,
    base_record_id="rec-wrong-base",
    new_record_id="rec-conflict-v2",
    revision=2,
    request_id="req-conflict-revise",
)
print("\n[请求入参（base_record_id 错误）]")
print(json.dumps(bad_revise, indent=2, ensure_ascii=False))
r9 = call_api("PUT", f"/v1/knowledge/entries/{conflict_pub}", bad_revise)
print(f"\n[响应] HTTP {r9['status_code']}")
print(json.dumps(r9["body"], indent=2, ensure_ascii=False))
save("09_bad_base_record", {"request": bad_revise, "response_status": r9["status_code"], "response_body": r9["body"]})


# 用例 #10：03.2 查询不存在的 publication（→ 404）
section("用例 #10：03.2 查询不存在的 publication_id（预期 404 NOT_FOUND）")
query_not_exist = make_query_payload(record_id="rec-nonexistent", request_id="req-kb-notfound")
print("\n[请求入参]")
print(json.dumps(query_not_exist, indent=2, ensure_ascii=False))
r10 = call_api("POST", "/v1/knowledge/entries/pub-nonexistent-999/query", query_not_exist)
print(f"\n[响应] HTTP {r10['status_code']}")
print(json.dumps(r10["body"], indent=2, ensure_ascii=False))
save("10_not_found", {"request": query_not_exist, "response_status": r10["status_code"], "response_body": r10["body"]})


# 用例 #11：03.1 必填字段缺失（→ 400）
section("用例 #11：03.1 必填字段缺失（预期 400 INVALID_REQUEST）")
incomplete_payload = {
    "request_id": "req-kb-incomplete",
    "tenant_id": "gsi-default",
    "resource_type": "document",
    "publication_id": "pub-test-incomplete",
}
print("\n[请求入参（缺少多个必填字段）]")
print(json.dumps(incomplete_payload, indent=2, ensure_ascii=False))
r11 = call_api("POST", "/v1/knowledge/entries", incomplete_payload)
print(f"\n[响应] HTTP {r11['status_code']}")
print(json.dumps(r11["body"], indent=2, ensure_ascii=False))
save("11_missing_fields", {"request": incomplete_payload, "response_status": r11["status_code"], "response_body": r11["body"]})


# 用例 #12：03.1 chunks.seq 不连续（→ 400）
section("用例 #12：03.1 chunks.seq 不连续（预期 400 INVALID_REQUEST）")
bad_seq_chunks = [
    {"seq": 1, "heading": "第一段", "content": "内容一", "page_from": 1, "page_to": 1},
    {"seq": 3, "heading": "第三段", "content": "内容三（seq 跳过了 2）", "page_from": 2, "page_to": 2},
]
bad_seq_payload = make_create_payload(
    publication_id="pub-test-badseq-001",
    record_id="rec-badseq-v1",
    revision=1,
    request_id="req-kb-badseq",
)
bad_seq_payload["chunks"] = bad_seq_chunks
bad_seq_payload["content_hash"] = compute_content_hash(bad_seq_chunks)
print("\n[请求入参（seq 从 1 跳到 3）]")
print(json.dumps(bad_seq_payload, indent=2, ensure_ascii=False))
r12 = call_api("POST", "/v1/knowledge/entries", bad_seq_payload)
print(f"\n[响应] HTTP {r12['status_code']}")
print(json.dumps(r12["body"], indent=2, ensure_ascii=False))
save("12_bad_seq", {"request": bad_seq_payload, "response_status": r12["status_code"], "response_body": r12["body"]})


# 用例 #13：03.4 删除不存在的 publication（→ 404）
section("用例 #13：03.4 删除不存在的 publication_id（预期 404 NOT_FOUND）")
delete_not_exist = make_delete_payload(
    publication_id="pub-nonexistent-999",
    record_id="rec-nonexistent",
    revision=2,
    request_id="req-kb-delete-notfound",
)
print("\n[请求入参]")
print(json.dumps(delete_not_exist, indent=2, ensure_ascii=False))
r13 = call_api("DELETE", "/v1/knowledge/entries/pub-nonexistent-999", delete_not_exist)
print(f"\n[响应] HTTP {r13['status_code']}")
print(json.dumps(r13["body"], indent=2, ensure_ascii=False))
save("13_delete_not_found", {"request": delete_not_exist, "response_status": r13["status_code"], "response_body": r13["body"]})


# 用例 #14：03.1 resource_type 非法（→ 400）
section("用例 #14：03.1 resource_type 非法值（预期 400 INVALID_REQUEST）")
bad_type_payload = make_create_payload(
    publication_id="pub-test-badtype-001",
    record_id="rec-badtype-v1",
    revision=1,
    request_id="req-kb-badtype",
)
bad_type_payload["resource_type"] = "invalid_type"
print("\n[请求入参（resource_type = 'invalid_type'）]")
print(json.dumps(bad_type_payload, indent=2, ensure_ascii=False))
r14 = call_api("POST", "/v1/knowledge/entries", bad_type_payload)
print(f"\n[响应] HTTP {r14['status_code']}")
print(json.dumps(r14["body"], indent=2, ensure_ascii=False))
save("14_bad_resource_type", {"request": bad_type_payload, "response_status": r14["status_code"], "response_body": r14["body"]})


# ══════════════════════════════════════════════════════════════════════
#  保存结果
# ══════════════════════════════════════════════════════════════════════

section("测试执行完成！保存结果...")
output_path = os.path.join(os.path.dirname(__file__), "test_results_knowledge.json")
with open(output_path, 'w', encoding='utf-8') as f:
    json.dump(RESULTS, f, indent=2, ensure_ascii=False)
print(f"结果已保存到 {output_path}")

# 打印汇总
section("测试结果汇总")
for name, data in RESULTS.items():
    status = data.get("response_status", "?")
    body = data.get("response_body", {})
    error_code = body.get("error", {}).get("code", "-")
    success = body.get("success", body.get("status", "-"))
    print(f"  {name}: HTTP {status} | error_code={error_code} | success/status={success}")
