"""重复治理（#16 / D06 / #17 / #18 / #19）与停用（#20）契约测试脚本。

```bash
# 全量执行
cd backend && .venv\\Scripts\\python.exe -m tests.contract.run_duplicate_tests

# 只跑指定用例：位置参数按「用例标签 / 请求文件名」子串匹配，未命中的用例只打印跳过
cd backend && .venv\\Scripts\\python.exe -m tests.contract.run_duplicate_tests chunk_check
```

前置数据（可重复执行，幂等台账按原响应回放）：

- **主篇**：`04_knowledge_search_prepare_entry.json` 通过 03.1 写入 GSI 发布单元
  `0876a2b0-2788-4816-a1fd-7f26a2bfd061`（resource_id `63858256ba974ede`）；
- **姊妹篇**：`05_duplicates_prepare_twin.json` 写入 `pub-test-dup-twin-001`，切片 1、2 与主篇
  逐字相同、切片 3 换成参考文献，于是 D06 的重复占比恰好 2/3（契约示例值 `0.6666666666666666`）；
- **停用篇**：`05_duplicates_prepare_deactivate.json` 写入 `pub-test-deact-001`，只给 #20 使用，
  不得被其它用例引用（停用后不可检索）。

四类占位符在发送前解析（口径只有服务端一份，脚本不自带实现）：

- `__COMPUTED_AT_RUNTIME__` → `client.resolve_content_hash()`（铺场正文摘要）；
- `__HASH_MAIN__` / `__HASH_TWIN__` / `__HASH_DRAFT__` → `/v1/test/content-hash` 实算：
  scope 里的 `content_hash` 必须与库内 revision 一致，写死必然漂移，不一致回 409；
- `__SCOPE_ISSUED_AT__` / `__SCOPE_EXPIRES_120S__` → 发送时刻的 Unix 毫秒（治理范围 120 秒跨度）；
- `__RESULT_ID__` / `__GROUP_ID__` / `__KEEP_HIT_ID__` / `__OTHER_HIT_ID__` → 从 #17 首查响应注入：
  这些标识由内容身份确定性推导，只有服务端算得出来（详情与预览都必须引用同一份快照）。

**前置缺失不伪造成功**：#17 首查拿不到 `result_id` 时，后续用例打印「跳过 + 原因」而不是发出
未解析的占位符（那只会换回 400，看起来像「接口坏了」，而不是「没铺场」）。
"""

import json
import sys
import time
from typing import Any

from tests.contract import case_data, client
from tests.contract.client import call_api

sys.stdout.reconfigure(encoding="utf-8")

RESULTS: dict[str, dict] = {}

# 用例过滤器：省略时全量执行；给了就只跑命中的用例（未跑到的 responses 文件保持原样）
ONLY: str = sys.argv[1].strip() if len(sys.argv) > 1 else ""

DUP_PATH = "/v1/knowledge/duplicates"
ENTRIES_PATH = "/v1/knowledge/entries"

MAIN_REQUEST = "04_knowledge_search_prepare_entry.json"
TWIN_REQUEST = "05_duplicates_prepare_twin.json"
DRAFT_CANDIDATE_REQUEST = "05_duplicates_check.json"

#: #17 响应里取出的运行时标识，供 #17 翻页 / #18 / #19 复用
RUNTIME: dict[str, Any] = {}
_HASH_CACHE: dict[str, str] = {}


def section(title: str) -> None:
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def save(response_file: str, data: dict) -> None:
    """按同名请求文件名收集：responses/ 与 requests/ 一一对应（含编号）。"""
    RESULTS[response_file] = data


def _hash_of(key: str, chunks: list[dict]) -> str:
    """按切片实算摘要（服务端口径）：同一份切片在一个进程里只算一次。"""
    if key not in _HASH_CACHE:
        _HASH_CACHE[key] = client.compute_content_hash({"chunks": chunks})
    return _HASH_CACHE[key]


def runtime_values() -> dict[str, Any]:
    """当前可解析的占位符取值：摘要与范围时效每次重算，`RUNTIME` 由 #17 首查填充。"""
    now = int(time.time() * 1000)
    return {
        "__SCOPE_ISSUED_AT__": now - 5_000,
        "__SCOPE_EXPIRES_120S__": now + 115_000,
        "__HASH_MAIN__": _hash_of("main", case_data.load_request(MAIN_REQUEST)["chunks"]),
        "__HASH_TWIN__": _hash_of("twin", case_data.load_request(TWIN_REQUEST)["chunks"]),
        "__HASH_DRAFT__": _hash_of(
            "draft", case_data.load_request(DRAFT_CANDIDATE_REQUEST)["candidate"]["chunks"]
        ),
        **RUNTIME,
    }


def _resolve(value: Any, values: dict[str, Any]) -> Any:
    """递归替换占位符：只替换「整个字符串就是一个占位符」的位置，不碰正文。"""
    if isinstance(value, str):
        return values.get(value, value)
    if isinstance(value, list):
        return [_resolve(item, values) for item in value]
    if isinstance(value, dict):
        return {key: _resolve(item, values) for key, item in value.items()}
    return value


def _unresolved(value: Any, values: dict[str, Any]) -> list[str]:
    """还剩下哪些占位符没值：缺值就用例跳过，绝不把 `__X__` 原样发出去换 400。"""
    if isinstance(value, str):
        return [value] if value.startswith("__") and value.endswith("__") and value not in values else []
    if isinstance(value, list):
        return [name for item in value for name in _unresolved(item, values)]
    if isinstance(value, dict):
        return [name for item in value.values() for name in _unresolved(item, values)]
    return []


def run_case(
    case_label: str,
    method: str,
    path: str,
    file_name: str,
    purpose: str | None = None,
) -> dict:
    """执行一个用例：解析占位符 → 打印入参 → 发出请求 → 打印出参，按请求文件名记录结果。"""
    if ONLY and ONLY not in case_label and ONLY not in file_name:
        print(f"[跳过] {case_label}（用例标签与 {file_name} 均未匹配 ONLY={ONLY}）")
        return {}
    values = runtime_values()
    payload = _resolve(client.resolve_content_hash(case_data.load_request(file_name)), values)
    resolved_path = _resolve(path, values)
    missing = sorted(set(_unresolved(payload, values) + _unresolved(resolved_path, values)))
    if missing:
        print(f"[跳过] {case_label}：缺少运行时取值 {missing}，请先让 #17 首查成功并返回分组")
        return {}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    print(f"\n[请求] {case_label} | {method} {resolved_path}（数据：{file_name}）")
    print("\n[请求入参]")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    result = call_api(method, resolved_path, body)
    print(f"\n[响应] HTTP {result['status_code']}")
    print(json.dumps(result["body"], indent=2, ensure_ascii=False))
    record = {
        "request_file": file_name,
        "request_path": f"{method} {resolved_path}",
        "request": payload,
        "response_status": result["status_code"],
        "response_body": result["body"],
    }
    if purpose:
        record["purpose"] = purpose
    save(file_name, record)
    return result


def capture_group_ids(result: dict) -> None:
    """从 #17 首查响应取出 #17 翻页 / #18 / #19 需要的标识。

    优先取 ``doc_exact`` 分组：成员是整篇知识，`full_text` 与 `evidence` 最能体现
    「详情补齐全文」的价值；没有时退化为第一个分组（例如只开了 `chunk` 口径）。
    """
    body = result.get("body") or {}
    groups = body.get("groups") or []
    if result.get("status_code") != 200 or not groups:
        print("[警告] #17 首查未返回分组：翻页 / 详情 / 预览用例将跳过（不伪造成功）")
        return
    RUNTIME["__RESULT_ID__"] = body["result_id"]
    chosen = next((group for group in groups if group.get("kind") == "doc_exact"), groups[0])
    keep_hit_id = chosen.get("suggested_keep_id")
    others = [item["hit_id"] for item in chosen.get("items") or [] if item["hit_id"] != keep_hit_id]
    if not keep_hit_id or not others:
        print(f"[警告] 分组 {chosen.get('group_id')} 不足两个成员：#19 合并预览将跳过")
        return
    RUNTIME["__GROUP_ID__"] = chosen["group_id"]
    RUNTIME["__KEEP_HIT_ID__"] = keep_hit_id
    RUNTIME["__OTHER_HIT_ID__"] = others[0]
    print(
        f"  [注入] result_id={RUNTIME['__RESULT_ID__']} group_id={RUNTIME['__GROUP_ID__']} "
        f"keep={keep_hit_id} other={others[0]}"
    )


# ══════════════════════════════════════════════════════════════════════
#  铺场：姊妹篇（与主篇 2/3 切片相同）与停用专用篇
# ══════════════════════════════════════════════════════════════════════

section("铺场 #1：03.1 新增姊妹篇（POST /v1/knowledge/entries，publication_id = pub-test-dup-twin-001）")
run_case(
    "00_prepare_twin",
    "POST",
    ENTRIES_PATH,
    TWIN_REQUEST,
    purpose=(
        "铺场：切片 1、2 与主篇逐字相同、切片 3 为参考文献，供 #17 出 chunk/doc_exact 分组，"
        "供 D06 得到 duplicate_chunks=2 / total_chunks=3"
    ),
)

section("铺场 #2：03.1 新增停用专用问答（POST /v1/knowledge/entries，publication_id = pub-test-deact-001）")
run_case(
    "01_prepare_deactivate",
    "POST",
    ENTRIES_PATH,
    "05_duplicates_prepare_deactivate.json",
    purpose="铺场：#20 需要一条可停用的已生效知识；该发布单元不得被其它用例引用",
)


# ══════════════════════════════════════════════════════════════════════
#  #16 保存前查重（候选为未入库草稿）
# ══════════════════════════════════════════════════════════════════════

section("用例 #1：#16 保存前查重（预期 200，is_duplicate=true，整篇与切片均有命中）")
run_case(
    "01_check_duplicate",
    "POST",
    f"{DUP_PATH}/check",
    DRAFT_CANDIDATE_REQUEST,
    purpose=(
        "候选草稿的标题与正文取自主篇：应命中主篇与姊妹篇的整篇（doc_exact）与相同切片（chunk）；"
        "D01 不写任何表，也不阻止 GSI 保存"
    ),
)


# ══════════════════════════════════════════════════════════════════════
#  #17 首查 → 翻页；#18 详情；#19 合并预览（三步看同一份结果）
# ══════════════════════════════════════════════════════════════════════

section("用例 #2：#17 查询重复分组首查（预期 200，落 30 分钟只读结果）")
groups_result = run_case(
    "02_groups_first",
    "POST",
    f"{DUP_PATH}/groups/query",
    "05_duplicates_groups_query.json",
    purpose="首查做全量检测并落快照：coverage.requested_resources 必须等于 evaluated_resources（2）",
)
capture_group_ids(groups_result)

section("用例 #3：#17 翻页（预期 200，result_id 与首查相同、只读同一份快照）")
run_case(
    "03_groups_page2",
    "POST",
    f"{DUP_PATH}/groups/query",
    "05_duplicates_groups_page2.json",
    purpose="带 result_id + cursor 翻页：不重算，分组必须与首查同一批（结果过期回 409 RESULT_EXPIRED）",
)

section("用例 #4：#17 非法游标（预期 400 INVALID_REQUEST）")
run_case(
    "04_groups_bad_cursor",
    "POST",
    f"{DUP_PATH}/groups/query",
    "05_duplicates_groups_bad_cursor.json",
    purpose="游标由本侧签发，非法游标必须报错而不是从头再来（否则前端会重复看到第一页）",
)

section("用例 #5：#18 分组详情（预期 200，items[].full_text + evidence 补齐）")
run_case(
    "05_group_detail",
    "POST",
    f"{DUP_PATH}/groups/__GROUP_ID__/query",
    "05_duplicates_group_detail.json",
    purpose="详情用实时来源补齐全文并校验成员未被修订：引用必须能在 full_text 里找到",
)

section("用例 #6：#19 合并预览（预期 200，preview_id/preview_hash + 冲突与告警）")
run_case(
    "06_merge_preview",
    "POST",
    f"{DUP_PATH}/merge-preview",
    "05_duplicates_merge_preview.json",
    purpose="只产出草稿态预览，不动正式库；主项动作必须是 keep，丢弃成员要出现在 warnings 里",
)


# ══════════════════════════════════════════════════════════════════════
#  D06 单篇切片查重（正向 2/3 + 负向越权）
# ══════════════════════════════════════════════════════════════════════

section("用例 #7：D06 单篇切片查重（预期 200，duplicate_chunks=2 / total_chunks=3）")
run_case(
    "07_chunk_check",
    "POST",
    f"{DUP_PATH}/chunk-check",
    "05_duplicates_chunk_check.json",
    purpose=(
        "candidate 是姊妹篇：切片 1、2 与主篇逐字相同、切片 3 为参考文献，"
        "duplicate_ratio 应为 0.6666666666666666（契约示例值），matches 逐对回传两侧完整正文"
    ),
)

section("用例 #8：D06 candidate 不在授权范围内（预期 400 INVALID_REQUEST）")
run_case(
    "08_chunk_check_out_of_scope",
    "POST",
    f"{DUP_PATH}/chunk-check",
    "05_duplicates_chunk_check_out_of_scope.json",
    purpose="candidate_resource 必须与 scope.resources 中一项完全一致：越权检测必须拒绝，不能回「未发现重复」",
)


# ══════════════════════════════════════════════════════════════════════
#  #20 停用已生效知识
# ══════════════════════════════════════════════════════════════════════

section("用例 #9：#20 停用（预期 200，deactivated=true / searchable=false）")
run_case(
    "09_deactivate",
    "POST",
    f"{ENTRIES_PATH}/pub-test-deact-001/deactivate",
    "05_duplicates_deactivate.json",
    purpose="停用后不参与检索与引用但保留正文供审计；重复调用按幂等台账回放同一份回执",
)


# ══════════════════════════════════════════════════════════════════════
#  保存结果
# ══════════════════════════════════════════════════════════════════════

section("测试执行完成！回写结果（responses/ 与 requests/ 同名一一对应）...")
for response_file, payload in RESULTS.items():
    case_data.save_result(response_file, payload)
    print(f"  {response_file}")


def consistency(file_name: str, data: dict) -> str:
    """按接口各自的自洽口径核对：只有 HTTP 200 不算过，字段之间也必须对得上。"""
    body = data.get("response_body") or {}
    if data.get("response_status") != 200:
        return "-"
    if file_name == "05_duplicates_check.json" and "matches" in body:
        return "ok" if body.get("is_duplicate") == bool(body["matches"]) else "FAIL"
    if file_name == "05_duplicates_chunk_check.json" and "matches" in body:
        total = body.get("total_chunks") or 0
        seqs = {item.get("source_seq") for item in body["matches"]}
        ratio = body.get("duplicate_ratio") or 0.0
        ok = (
            body.get("complete") is True
            and body.get("evaluated_resources") == 2
            and body.get("duplicate_chunks") == len(seqs)
            and total == 3
            and abs(ratio - len(seqs) / total) < 1e-9
        )
        return "ok" if ok else "FAIL"
    if file_name == "05_duplicates_groups_query.json" and "groups" in body:
        coverage = body.get("coverage") or {}
        ok = coverage.get("requested_resources") == coverage.get("evaluated_resources") == 2
        return "ok" if ok else "FAIL"
    return "-"


section("测试结果汇总")
for file_name, data in RESULTS.items():
    body = data.get("response_body", {})
    error_code = body.get("error", {}).get("code", "-")
    detail = body.get("error", {}).get("message") if error_code != "-" else body.get("success", body.get("status", "-"))
    print(
        f"  {file_name:52} HTTP {data.get('response_status')} | error_code={error_code} | "
        f"consistency={consistency(file_name, data)} | {detail}"
    )
