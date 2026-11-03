"""解析接口契约测试脚本 v2：使用全新 job_id，输出严格的真实出入参。"""

import httpx
import json
import time
import sys
import os

sys.stdout.reconfigure(encoding='utf-8')

BASE_URL = "http://127.0.0.1:8002"
RESULTS = {}


def get_signed_headers(method: str, path: str, body: str = "") -> dict:
    resp = httpx.post(f"{BASE_URL}/v1/test/signed-headers", json={
        "method": method, "path": path, "body": body,
    })
    return resp.json()["headers"]


def submit_parse_job(payload: dict) -> dict:
    body_str = json.dumps(payload, ensure_ascii=False)
    headers = get_signed_headers("POST", "/v1/parse/jobs", body_str)
    headers["Content-Type"] = "application/json"
    resp = httpx.post(f"{BASE_URL}/v1/parse/jobs", content=body_str, headers=headers)
    return {"status_code": resp.status_code, "headers": dict(resp.headers), "body": resp.json()}


def query_parse_job(job_id: str) -> dict:
    path = f"/v1/parse/jobs/{job_id}"
    headers = get_signed_headers("GET", path)
    resp = httpx.get(f"{BASE_URL}{path}", headers=headers)
    return {"status_code": resp.status_code, "headers": dict(resp.headers), "body": resp.json()}


def wait_for_completion(job_id: str, timeout: int = 600, interval: int = 5) -> dict:
    start = time.time()
    last = None
    while time.time() - start < timeout:
        last = query_parse_job(job_id)
        status = last["body"].get("status")
        progress = last["body"].get("progress", 0)
        print(f"  [{job_id}] 状态={status} 进度={progress}%")
        if status in ("succeeded", "failed"):
            return last
        time.sleep(interval)
    return last


def save(name, data):
    RESULTS[name] = data


# ==================== 用例 #1：文档解析提交 ====================
print("=" * 70)
print("用例 #1：文档解析提交 (#01 POST /v1/parse/jobs)")
print("=" * 70)

doc_payload = {
    "job_id": "parse-doc-100001",
    "doc_id": "doc-demo-10001",
    "version_id": "v1",
    "file_url": "https://cdn-mineru.openxlab.org.cn/demo/example.pdf",
    "file_name": "Lane_2005_造林与流量历时曲线.pdf",
    "options": {"chunk": True, "language": "zh"},
    "tenant_id": "gsi-default",
    "resource_type": "document",
    "file_size": 1024,
    "metadata": {"title": "造林对流量历时曲线的影响"}
}

print("\n[请求入参]")
print(json.dumps(doc_payload, indent=2, ensure_ascii=False))

r1 = submit_parse_job(doc_payload)
print(f"\n[响应] HTTP {r1['status_code']}")
print(json.dumps(r1["body"], indent=2, ensure_ascii=False))
save("01_doc_submit", {"request": doc_payload, "response_status": r1["status_code"], "response_body": r1["body"]})

# 等待完成
print("\n等待文档解析完成...")
r1_final = wait_for_completion("parse-doc-100001")
print(f"\n[最终查询响应] HTTP {r1_final['status_code']}")
print(json.dumps(r1_final["body"], indent=2, ensure_ascii=False))
save("01_doc_final", {"response_status": r1_final["status_code"], "response_body": r1_final["body"]})


# ==================== 用例 #2：FAQ 解析提交 ====================
print("\n" + "=" * 70)
print("用例 #2：FAQ 解析提交 (#01 POST /v1/parse/jobs)")
print("=" * 70)

faq_payload = {
    "job_id": "parse-faq-200001",
    "doc_id": "doc-demo-20001",
    "version_id": "1",
    "file_url": "https://cdn-mineru.openxlab.org.cn/demo/example.pdf",
    "file_name": "example.pdf",
    "prompt_text": "请基于文档内容，严格依据原文提炼覆盖研究目的、方法、数据、结果、结论与局限性的中文FAQ问答对，答案准确完整并可含关键公式、数值和术语，每问独立有意义、避免重复宽泛，按标准FAQ格式输出。",
    "model_key": "default",
    "options": {"chunk": True, "language": "中文"},
    "tenant_id": "gsi-default",
    "resource_type": "faq",
    "file_id": "108",
    "file_size": 102400,
    "metadata": {
        "title": "造林对流量历时曲线的影响",
        "doc_type": "FAQ",
        "owner": "kali03",
        "author": "kali03",
        "team": "TEAM-IT",
        "language": "中文",
        "trust": "A",
        "summary": "VPN申请与开通",
        "content": "VPN申请流程",
        "tags": ["VPN"],
        "expire_at": "2027-12-31",
        "scheduled_publish_at": "2026-11-01T09:00:00+08:00",
        "invisible_teams": ["TEAM-EXCLUDED"],
        "audience": {
            "department": ["IT"],
            "team": ["TEAM-IT"],
            "role": ["工程师"],
            "region": ["中国"],
            "skill": ["基础"],
            "scenario": ["VPN申请"]
        },
        "cover_url": "https://files.example.com/cover.png",
        "cover_name": "cover.png",
        "linked_doc_id": "doc-source-001",
        "linked_section": "VPN申请",
        "change_impact_level": "minor",
        "units": [
            {"unit_id": "unit-1", "no": "U001", "title": "申请步骤", "kind": "text", "text": "在服务台提交申请。"}
        ]
    }
}

print("\n[请求入参]")
print(json.dumps(faq_payload, indent=2, ensure_ascii=False))

r2 = submit_parse_job(faq_payload)
print(f"\n[响应] HTTP {r2['status_code']}")
print(json.dumps(r2["body"], indent=2, ensure_ascii=False))
save("02_faq_submit", {"request": faq_payload, "response_status": r2["status_code"], "response_body": r2["body"]})

# 等待完成
print("\n等待FAQ解析完成...")
r2_final = wait_for_completion("parse-faq-200001")
print(f"\n[最终查询响应] HTTP {r2_final['status_code']}")
print(json.dumps(r2_final["body"], indent=2, ensure_ascii=False))
save("02_faq_final", {"response_status": r2_final["status_code"], "response_body": r2_final["body"]})


# ==================== 用例 #5：文件类型不支持 ====================
print("\n" + "=" * 70)
print("用例 #5：文件类型不支持 (#01 POST /v1/parse/jobs)")
print("=" * 70)

invalid_type_payload = {
    "job_id": "parse-invalid-type-001",
    "doc_id": "doc-demo-10001",
    "version_id": "v1",
    "file_url": "https://cdn-mineru.openxlab.org.cn/demo/example.pdf",
    "file_name": "readme.txt",
    "options": {"chunk": True, "language": "zh"},
    "tenant_id": "gsi-default",
    "resource_type": "document",
    "file_size": 1024,
    "metadata": {"title": "测试不支持文件类型"}
}

print("\n[请求入参]")
print(json.dumps(invalid_type_payload, indent=2, ensure_ascii=False))

r5 = submit_parse_job(invalid_type_payload)
print(f"\n[响应] HTTP {r5['status_code']}")
print(json.dumps(r5["body"], indent=2, ensure_ascii=False))
save("05_invalid_type", {"request": invalid_type_payload, "response_status": r5["status_code"], "response_body": r5["body"]})


# ==================== 用例 #6：文件超过 10MB ====================
print("\n" + "=" * 70)
print("用例 #6：文件超过 10MB (#01 POST /v1/parse/jobs)")
print("=" * 70)

too_large_payload = {
    "job_id": "parse-too-large-001",
    "doc_id": "doc-demo-10001",
    "version_id": "v1",
    "file_url": "https://cdn-mineru.openxlab.org.cn/demo/example.pdf",
    "file_name": "Lane_2005_造林与流量历时曲线.pdf",
    "options": {"chunk": True, "language": "zh"},
    "tenant_id": "gsi-default",
    "resource_type": "document",
    "file_size": 11000000,
    "metadata": {"title": "造林对流量历时曲线的影响"}
}

print("\n[请求入参]")
print(json.dumps(too_large_payload, indent=2, ensure_ascii=False))

r6 = submit_parse_job(too_large_payload)
print(f"\n[响应] HTTP {r6['status_code']}")
print(json.dumps(r6["body"], indent=2, ensure_ascii=False))
save("06_too_large", {"request": too_large_payload, "response_status": r6["status_code"], "response_body": r6["body"]})


# ==================== 用例 #7：FAQ 缺失 prompt_text ====================
print("\n" + "=" * 70)
print("用例 #7：FAQ 缺失 prompt_text (#01 POST /v1/parse/jobs)")
print("=" * 70)

missing_prompt_payload = {
    "job_id": "parse-missing-prompt-001",
    "doc_id": "doc-demo-20001",
    "version_id": "1",
    "file_url": "https://cdn-mineru.openxlab.org.cn/demo/example.pdf",
    "file_name": "example.pdf",
    "model_key": "default",
    "options": {"chunk": True, "language": "中文"},
    "tenant_id": "gsi-default",
    "resource_type": "faq",
    "file_id": "108",
    "file_size": 102400,
    "metadata": {
        "title": "造林对流量历时曲线的影响",
        "doc_type": "FAQ",
        "owner": "kali03",
        "author": "kali03",
        "team": "TEAM-IT",
        "language": "中文",
        "trust": "A",
        "summary": "VPN申请与开通",
        "content": "VPN申请流程",
        "tags": ["VPN"]
    }
}

print("\n[请求入参]")
print(json.dumps(missing_prompt_payload, indent=2, ensure_ascii=False))

r7 = submit_parse_job(missing_prompt_payload)
print(f"\n[响应] HTTP {r7['status_code']}")
print(json.dumps(r7["body"], indent=2, ensure_ascii=False))
save("07_missing_prompt", {"request": missing_prompt_payload, "response_status": r7["status_code"], "response_body": r7["body"]})


# ==================== 用例 #8：任务不存在 ====================
print("\n" + "=" * 70)
print("用例 #8：任务不存在 (#02 GET /v1/parse/jobs/{job_id})")
print("=" * 70)

print("\n[请求] GET /v1/parse/jobs/non-existent-job-id")
r8 = query_parse_job("non-existent-job-id")
print(f"\n[响应] HTTP {r8['status_code']}")
print(json.dumps(r8["body"], indent=2, ensure_ascii=False))
save("08_not_found", {"request_job_id": "non-existent-job-id", "response_status": r8["status_code"], "response_body": r8["body"]})


# ==================== 用例 #9：幂等重试 ====================
print("\n" + "=" * 70)
print("用例 #9：幂等重试（与用例 #1 相同入参）(#01 POST /v1/parse/jobs)")
print("=" * 70)

print("\n[请求入参]（与用例 #1 完全相同）")
print(json.dumps(doc_payload, indent=2, ensure_ascii=False))

r9 = submit_parse_job(doc_payload)
print(f"\n[响应] HTTP {r9['status_code']}")
print(json.dumps(r9["body"], indent=2, ensure_ascii=False))
save("09_idempotent", {"request": doc_payload, "response_status": r9["status_code"], "response_body": r9["body"]})


# ==================== 保存结果 ====================
print("\n" + "=" * 70)
print("测试执行完成！保存结果...")
print("=" * 70)

output_path = os.path.join(os.path.dirname(__file__), "test_results_v2.json")
with open(output_path, 'w', encoding='utf-8') as f:
    json.dump(RESULTS, f, indent=2, ensure_ascii=False)
print(f"结果已保存到 {output_path}")
