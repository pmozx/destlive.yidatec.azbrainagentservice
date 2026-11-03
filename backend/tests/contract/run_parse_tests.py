"""解析接口契约测试脚本：执行 #01 / #02 接口的完整测试用例。"""

import httpx
import json
import time
import sys

# 设置输出编码
sys.stdout.reconfigure(encoding='utf-8')

BASE_URL = "http://127.0.0.1:8002"
RESULTS = []  # 存储测试结果


def get_signed_headers(method: str, path: str, body: str = "") -> dict:
    """调用测试辅助接口生成签名头"""
    resp = httpx.post(f"{BASE_URL}/v1/test/signed-headers", json={
        "method": method,
        "path": path,
        "body": body,
    })
    return resp.json()["headers"]


def submit_parse_job(payload: dict) -> dict:
    """提交解析任务"""
    body_str = json.dumps(payload, ensure_ascii=False)
    headers = get_signed_headers("POST", "/v1/parse/jobs", body_str)
    headers["Content-Type"] = "application/json"
    resp = httpx.post(f"{BASE_URL}/v1/parse/jobs", content=body_str, headers=headers)
    return {"status_code": resp.status_code, "body": resp.json()}


def query_parse_job(job_id: str) -> dict:
    """查询解析任务状态"""
    headers = get_signed_headers("GET", f"/v1/parse/jobs/{job_id}")
    resp = httpx.get(f"{BASE_URL}/v1/parse/jobs/{job_id}", headers=headers)
    return {"status_code": resp.status_code, "body": resp.json()}


def wait_for_completion(job_id: str, timeout: int = 300, interval: int = 3) -> dict:
    """轮询等待任务完成"""
    start = time.time()
    while time.time() - start < timeout:
        result = query_parse_job(job_id)
        status = result["body"].get("status")
        if status in ("succeeded", "failed"):
            return result
        print(f"  状态: {status}, 进度: {result['body'].get('progress')}%")
        time.sleep(interval)
    return {"error": "timeout", "status_code": None, "body": None}


def save_result(test_name: str, request_data: dict, response_data: dict, final_result: dict = None):
    """保存测试结果"""
    result = {
        "test_name": test_name,
        "request": request_data,
        "response": response_data,
    }
    if final_result:
        result["final_result"] = final_result
    RESULTS.append(result)


def save_results_to_file(filename: str = "test_results.json"):
    """将测试结果保存到文件"""
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(RESULTS, f, indent=2, ensure_ascii=False)
    print(f"\n测试结果已保存到 {filename}")


# ==================== 测试用例 #1：文档解析提交 ====================
print("=" * 70)
print("用例 #1：文档解析提交")
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

print("\n请求入参：")
print(json.dumps(doc_payload, indent=2, ensure_ascii=False))

result = submit_parse_job(doc_payload)
print(f"\nHTTP {result['status_code']}")
print("响应出参：")
print(json.dumps(result["body"], indent=2, ensure_ascii=False))

# 等待任务完成
print("\n等待任务完成...")
final_result = wait_for_completion("parse-doc-100001")
print(f"\n最终结果（#02 查询）:")
print(json.dumps(final_result, indent=2, ensure_ascii=False))

# 保存结果
save_result("用例 #1：文档解析提交", doc_payload, result, final_result)


# ==================== 测试用例 #2：FAQ 解析提交 ====================
print("\n" + "=" * 70)
print("用例 #2：FAQ 解析提交")
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
            {
                "unit_id": "unit-1",
                "no": "U001",
                "title": "申请步骤",
                "kind": "text",
                "text": "在服务台提交申请。"
            }
        ]
    }
}

print("\n请求入参：")
print(json.dumps(faq_payload, indent=2, ensure_ascii=False))

result = submit_parse_job(faq_payload)
print(f"\nHTTP {result['status_code']}")
print("响应出参：")
print(json.dumps(result["body"], indent=2, ensure_ascii=False))

# 等待任务完成
print("\n等待任务完成...")
final_result = wait_for_completion("parse-faq-200001")
print(f"\n最终结果（#02 查询）:")
print(json.dumps(final_result, indent=2, ensure_ascii=False))

# 保存结果
save_result("用例 #2：FAQ 解析提交", faq_payload, result, final_result)


# ==================== 测试用例 #5：文件类型不支持 ====================
print("\n" + "=" * 70)
print("用例 #5：文件类型不支持")
print("=" * 70)

invalid_type_payload = doc_payload.copy()
invalid_type_payload["job_id"] = "parse-invalid-type-001"
invalid_type_payload["file_name"] = "readme.txt"

print("\n请求入参（file_name 改为 .txt）：")
print(json.dumps(invalid_type_payload, indent=2, ensure_ascii=False))

result = submit_parse_job(invalid_type_payload)
print(f"\nHTTP {result['status_code']}")
print("响应出参：")
print(json.dumps(result["body"], indent=2, ensure_ascii=False))
save_result("用例 #5：文件类型不支持", invalid_type_payload, result)


# ==================== 测试用例 #6：文件超过 10MB ====================
print("\n" + "=" * 70)
print("用例 #6：文件超过 10MB")
print("=" * 70)

too_large_payload = doc_payload.copy()
too_large_payload["job_id"] = "parse-too-large-001"
too_large_payload["file_size"] = 11000000

print("\n请求入参（file_size 改为 11MB）：")
print(json.dumps(too_large_payload, indent=2, ensure_ascii=False))

result = submit_parse_job(too_large_payload)
print(f"\nHTTP {result['status_code']}")
print("响应出参：")
print(json.dumps(result["body"], indent=2, ensure_ascii=False))
save_result("用例 #6：文件超过 10MB", too_large_payload, result)


# ==================== 测试用例 #7：FAQ 缺失 prompt_text ====================
print("\n" + "=" * 70)
print("用例 #7：FAQ 缺失 prompt_text")
print("=" * 70)

missing_prompt_payload = faq_payload.copy()
missing_prompt_payload["job_id"] = "parse-missing-prompt-001"
del missing_prompt_payload["prompt_text"]

print("\n请求入参（删除 prompt_text）：")
print(json.dumps(missing_prompt_payload, indent=2, ensure_ascii=False))

result = submit_parse_job(missing_prompt_payload)
print(f"\nHTTP {result['status_code']}")
print("响应出参：")
print(json.dumps(result["body"], indent=2, ensure_ascii=False))
save_result("用例 #7：FAQ 缺失 prompt_text", missing_prompt_payload, result)


# ==================== 测试用例 #8：任务不存在 ====================
print("\n" + "=" * 70)
print("用例 #8：任务不存在")
print("=" * 70)

print("\n请求：GET /v1/parse/jobs/non-existent-job-id")
result = query_parse_job("non-existent-job-id")
print(f"\nHTTP {result['status_code']}")
print("响应出参：")
print(json.dumps(result["body"], indent=2, ensure_ascii=False))
save_result("用例 #8：任务不存在", {"job_id": "non-existent-job-id"}, result)


# ==================== 测试用例 #9：幂等重试 ====================
print("\n" + "=" * 70)
print("用例 #9：幂等重试（与用例 #1 相同入参）")
print("=" * 70)

print("\n请求入参（与用例 #1 完全相同）：")
print(json.dumps(doc_payload, indent=2, ensure_ascii=False))

result = submit_parse_job(doc_payload)
print(f"\nHTTP {result['status_code']}")
print("响应出参：")
print(json.dumps(result["body"], indent=2, ensure_ascii=False))
save_result("用例 #9：幂等重试", doc_payload, result)


print("\n" + "=" * 70)
print("测试执行完成！")
print("=" * 70)

# 保存所有测试结果到文件
save_results_to_file("tests/contract/test_results.json")
