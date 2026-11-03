"""解析接口契约测试脚本（#01 提交解析任务 / #02 查询解析进度）。

```bash
cd backend && .venv\\Scripts\\python.exe -m tests.contract.run_parse_tests
```

入参不写在脚本里：一律从 `00-document/06-.../test-data/requests/` 读文件字节，
再把**同一份字节**交给签名接口和业务接口，避免「脚本一份、文档一份」的漂移。
实测结果按「请求文件 → 同名响应文件」逐件回写 `test-data/responses/`：
`requests/01_parse_jobs_doc.json` 的实测结果就是 `responses/01_parse_jobs_doc.json`，
文件名与编号完全对应，报告引用时不需要再在汇总文件里查 key。
"""

import json
import sys

from tests.contract import case_data
from tests.contract.client import call_api, wait_parse_job

sys.stdout.reconfigure(encoding="utf-8")

# 键为「与 requests/ 同名的响应文件名」，最后一件一条落盘
RESULTS: dict[str, dict] = {}

PARSE_SUBMIT_PATH = "/v1/parse/jobs"


def section(title: str) -> None:
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def save(response_file: str, data: dict) -> None:
    """按同名请求文件名收集：responses/ 与 requests/ 一一对应（含编号）。"""
    RESULTS[response_file] = data


def show(payload: dict) -> None:
    print("\n[请求入参]")
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def submit(file_name: str) -> tuple[dict, dict]:
    """提交解析任务：请求正文取文件原始字节（含缩进/换行），签名与发送用同一份。"""
    body = case_data.request_bytes(file_name)
    payload = json.loads(body.decode("utf-8"))
    show(payload)
    result = call_api("POST", PARSE_SUBMIT_PATH, body)
    print(f"\n[响应] HTTP {result['status_code']}")
    print(json.dumps(result["body"], indent=2, ensure_ascii=False))
    return payload, result


def finish(job_id: str) -> dict:
    """轮询 #02 直到任务结束，返回最后一次查询的原始响应。"""
    print(f"\n等待任务 {job_id} 完成...")
    return wait_parse_job(job_id)


def query_job(file_name: str, expect_job_id: str) -> tuple[str, dict]:
    """按 `02_*.json` 里的 `job_id` 拼 GET 路径并轮询到终态。

    `02_*` 请求文件只承载路径参数（GET 无请求正文），因此不参与签名摘要。
    """
    job_id = case_data.load_request(file_name)["job_id"]
    if job_id != expect_job_id:
        raise SystemExit(f"{file_name} 的 job_id={job_id} 与提交入参 {expect_job_id} 不一致")
    request_path = f"{PARSE_SUBMIT_PATH}/{job_id}"
    print(f"\n[请求入参] {file_name} → GET {request_path}（无请求正文）")
    return request_path, finish(job_id)


# ==================== 用例 #1：文档解析提交（基线） ====================
section("用例 #1：文档解析提交 (#01 POST /v1/parse/jobs) — 基线 01_parse_jobs_doc.json")

doc_payload, r1 = submit("01_parse_jobs_doc.json")
save("01_parse_jobs_doc.json", {
    "request_file": "01_parse_jobs_doc.json",
    "request": doc_payload,
    "response_status": r1["status_code"],
    "response_body": r1["body"],
})

doc_query_path, r1_final = query_job("02_parse_jobs_doc.json", doc_payload["job_id"])
print(f"\n[最终查询响应] HTTP {r1_final['status_code']}")
print(json.dumps(r1_final["body"], indent=2, ensure_ascii=False))
save("02_parse_jobs_doc.json", {
    "request_file": "02_parse_jobs_doc.json",
    "request_path": f"GET {doc_query_path}",
    "response_status": r1_final["status_code"],
    "response_body": r1_final["body"],
})


# ==================== 用例 #2：FAQ 解析提交（基线） ====================
section("用例 #2：FAQ 解析提交 (#01 POST /v1/parse/jobs) — 基线 01_parse_jobs_faq.json")

faq_payload, r2 = submit("01_parse_jobs_faq.json")
save("01_parse_jobs_faq.json", {
    "request_file": "01_parse_jobs_faq.json",
    "request": faq_payload,
    "response_status": r2["status_code"],
    "response_body": r2["body"],
})

faq_query_path, r2_final = query_job("02_parse_jobs_faq.json", faq_payload["job_id"])
print(f"\n[最终查询响应] HTTP {r2_final['status_code']}")
print(json.dumps(r2_final["body"], indent=2, ensure_ascii=False))
save("02_parse_jobs_faq.json", {
    "request_file": "02_parse_jobs_faq.json",
    "request_path": f"GET {faq_query_path}",
    "response_status": r2_final["status_code"],
    "response_body": r2_final["body"],
})


# ==================== 用例 #5：文件类型不支持 ====================
section("用例 #5：文件类型不支持 (#01 POST /v1/parse/jobs)")

payload, r5 = submit("01_parse_jobs_doc_unsupported_type.json")
save("01_parse_jobs_doc_unsupported_type.json", {
    "request_file": "01_parse_jobs_doc_unsupported_type.json",
    "request": payload,
    "response_status": r5["status_code"],
    "response_body": r5["body"],
})


# ==================== 用例 #6：文件超过 10MB ====================
section("用例 #6：文件超过 10MB (#01 POST /v1/parse/jobs)")

payload, r6 = submit("01_parse_jobs_doc_too_large.json")
save("01_parse_jobs_doc_too_large.json", {
    "request_file": "01_parse_jobs_doc_too_large.json",
    "request": payload,
    "response_status": r6["status_code"],
    "response_body": r6["body"],
})


# ==================== 用例 #7：FAQ 缺失 prompt_text ====================
section("用例 #7：FAQ 缺失 prompt_text (#01 POST /v1/parse/jobs)")

payload, r7 = submit("01_parse_jobs_faq_missing_prompt.json")
save("01_parse_jobs_faq_missing_prompt.json", {
    "request_file": "01_parse_jobs_faq_missing_prompt.json",
    "request": payload,
    "response_status": r7["status_code"],
    "response_body": r7["body"],
})


# ==================== 用例 #8：任务不存在 ====================
section("用例 #8：任务不存在 (#02 GET /v1/parse/jobs/{job_id})")

not_found_job_id = case_data.load_request("02_parse_jobs_not_found.json")["job_id"]
request_path = f"{PARSE_SUBMIT_PATH}/{not_found_job_id}"
print(f"\n[请求入参] 02_parse_jobs_not_found.json → GET {request_path}（无请求正文）")
r8 = call_api("GET", request_path)
print(f"\n[响应] HTTP {r8['status_code']}")
print(json.dumps(r8["body"], indent=2, ensure_ascii=False))
save("02_parse_jobs_not_found.json", {
    "request_file": "02_parse_jobs_not_found.json",
    "request_path": f"GET {request_path}",
    "response_status": r8["status_code"],
    "response_body": r8["body"],
})


# ==================== 用例 #9：幂等重试 ====================
section("用例 #9：幂等重试 (#01 POST /v1/parse/jobs) — 重复用例 #1 的同一份请求")

retry_payload, r9 = submit("01_parse_jobs_doc.json")
if retry_payload != doc_payload:
    raise SystemExit("重试正文与用例 #1 不一致，幂等前提不成立")
# 同一份请求文件 ⇒ 同一个响应文件：重试回执作为附加证据记在同一处
doc_receipt = RESULTS["01_parse_jobs_doc.json"]
doc_receipt["idempotent_retry"] = {
    "response_status": r9["status_code"],
    "response_body": r9["body"],
    "identical": r9["body"] == doc_receipt["response_body"],
}
print(f"\n[幂等] 重试回执与首次一致：{doc_receipt['idempotent_retry']['identical']}")


# ==================== 保存结果 ====================
section("测试执行完成！回写结果（responses/ 与 requests/ 同名一一对应）...")
for response_file, payload in RESULTS.items():
    case_data.save_result(response_file, payload)
    print(f"  {response_file}")
