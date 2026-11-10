"""会话与生成接口（#08–#15）契约测试脚本。

```bash
# 全量执行
cd backend && .venv\\Scripts\\python.exe -m tests.contract.run_chat_tests

# 只跑指定用例：位置参数按「用例标签 / 请求文件名」子串匹配，未命中的用例只打印跳过
cd backend && .venv\\Scripts\\python.exe -m tests.contract.run_chat_tests detail
```

入参一律来自 `00-document/06-.../test-data/requests/`：

- `authorization` 的 `issued_at` / `expires_at` 写占位符，由 `case_data.resolve_authorization()`
  在发送前换算成当前时刻的毫秒值（静态值一落地就过期）；
- `__SESSION_ID__` / `__TEMP_SESSION_ID__` 由本脚本从 #08 响应注入：`session_id` 由 AI 分配，
  无法预写；会话用**固定 request_id** 创建，重复执行时幂等台账回放同一个 `session_id`，
  后续用例才有稳定的会话可查。

**两个会话各司其职**，避免破坏性用例污染主链路：

- 主会话（`req-chat-session-create-001`）用于 #09 / #10 / #11 / #14 / #15；
- 临时会话（`req-chat-session-temp-001`）只用于 #13 清空与 #12 删除，删除后的 #10 预期 404。

实测结果按同名规则回写 `test-data/responses/{请求文件名}`（含编号，与 requests 一一对应）。
"""

import json
import re
import sys
from typing import Any

from tests.contract import case_data
from tests.contract.client import call_api

sys.stdout.reconfigure(encoding="utf-8")

RESULTS: dict[str, dict] = {}

# 用例过滤器：省略时全量执行；给了就只跑命中的用例（未跑到的 responses 文件保持原样）
ONLY: str = sys.argv[1].strip() if len(sys.argv) > 1 else ""

CHAT_PATH = "/v1/chat"
SESSIONS_PATH = f"{CHAT_PATH}/sessions"

#: #14 的 request_id 固定：既是幂等键，也是 #15 要查询的「原操作」ID
COMPLETION_REQUEST_ID = "req-chat-completion-001"

#: #08 响应里取出的会话 ID，供后续用例的路径与正文复用
RUNTIME: dict[str, str] = {}

#: 占位符形如 `__SESSION_ID__`；既可能独占一个字段值，也可能嵌在路径里（`/sessions/__SESSION_ID__/query`）
PLACEHOLDER = re.compile(r"__[A-Z][A-Z0-9_]*__")


def section(title: str) -> None:
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def save(response_file: str, data: dict) -> None:
    """按同名请求文件名收集：responses/ 与 requests/ 一一对应（含编号）。"""
    RESULTS[response_file] = data


def _resolve(value: Any, values: dict[str, str]) -> Any:
    """递归替换会话 ID 占位符：整串命中直接取映射值，嵌在路径里也按名替换。"""
    if isinstance(value, str):
        if value in values:
            return values[value]
        return PLACEHOLDER.sub(lambda match: values.get(match.group(0), match.group(0)), value)
    if isinstance(value, list):
        return [_resolve(item, values) for item in value]
    if isinstance(value, dict):
        return {key: _resolve(item, values) for key, item in value.items()}
    return value


def _unresolved(value: Any, values: dict[str, str]) -> list[str]:
    """还剩下哪些占位符没值：缺值就用例跳过，绝不把 `__X__` 原样发出去换 400。"""
    if isinstance(value, str):
        return [name for name in PLACEHOLDER.findall(value) if name not in values]
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
    save_as: str | None = None,
) -> dict:
    """执行一个用例：解析授权时效与会话 ID → 打印入参 → 发出请求 → 打印出参并记录结果。

    ``save_as`` 用于「同一份请求发两次」的幂等用例：第二次回执另存一个文件名，
    不覆盖首次记录（否则无法比对两次的 ``assistant_message_id``）。
    """
    if ONLY and ONLY not in case_label and ONLY not in file_name:
        print(f"[跳过] {case_label}（用例标签与 {file_name} 均未匹配 ONLY={ONLY}）")
        return {}
    values = dict(RUNTIME)
    payload = _resolve(case_data.resolve_authorization(case_data.load_request(file_name)), values)
    resolved_path = _resolve(path, values)
    missing = sorted(set(_unresolved(payload, values) + _unresolved(resolved_path, values)))
    if missing:
        print(f"[跳过] {case_label}：缺少运行时取值 {missing}，请先让 #08 创建会话成功")
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
    # 回放标记：命中幂等台账时服务端回 X-Idempotent-Replay: true，只有真回放才记
    replayed = any(
        key.lower() == "x-idempotent-replay" and str(value).lower() == "true"
        for key, value in (result.get("headers") or {}).items()
    )
    if replayed:
        record["idempotent_replay"] = True
    save(save_as or file_name, record)
    return result


def capture_session(result: dict, key: str, *, label: str) -> None:
    """从 #08 响应取出 `session_id`；拿不到就只告警，后续用例自行跳过。"""
    body = result.get("body") or {}
    session_id = body.get("session_id")
    if result.get("status_code") not in (200, 201) or not session_id:
        print(f"[警告] {label} 未返回 session_id：依赖它的用例将跳过（不伪造成功）")
        return
    RUNTIME[key] = session_id
    print(f"  [注入] {key} = {session_id}")


# ══════════════════════════════════════════════════════════════════════
#  会话：#08 创建（主 / 临时）→ #09 列表 → #10 详情 → #11 改名
# ══════════════════════════════════════════════════════════════════════

section("用例 #1：#08 创建会话（不带 authorization，手册标注可选；预期 201）")
main_create = run_case(
    "01_session_create",
    "POST",
    SESSIONS_PATH,
    "06_chat_session_create.json",
    purpose="session_id 由 AI 分配；不带授权块也必须能建（缺块本身是契约差异，不能一律放宽或一律必填）",
)
capture_session(main_create, "__SESSION_ID__", label="#08 主会话")

section("用例 #2：#08 创建临时会话（只用于 #13 清空与 #12 删除）")
temp_create = run_case(
    "02_session_create_temp",
    "POST",
    SESSIONS_PATH,
    "06_chat_session_create_temp.json",
    purpose="破坏性用例（清空 / 删除）只作用在临时会话上，重跑脚本不污染主会话",
)
capture_session(temp_create, "__TEMP_SESSION_ID__", label="#08 临时会话")

section("用例 #3：#09 分页查询我的会话（预期 200，只含本人会话）")
run_case(
    "03_session_list",
    "POST",
    f"{SESSIONS_PATH}/query",
    "06_chat_session_list.json",
    purpose="游标不透明：next_cursor 不能自行拼接；只返回签名身份自己的会话",
)

section("用例 #4：#10 会话详情（include_messages=false，预期 200 且不含 messages）")
run_case(
    "04_session_detail",
    "POST",
    f"{SESSIONS_PATH}/__SESSION_ID__/query",
    "06_chat_session_detail.json",
    purpose="条件必返字段：不要求历史时必须整体省略 messages / next_cursor，而不是回空数组",
)

section("用例 #5：#11 修改会话标题（预期 200，标题已更新）")
run_case(
    "05_session_rename",
    "PATCH",
    f"{SESSIONS_PATH}/__SESSION_ID__",
    "06_chat_session_rename.json",
    purpose="只改标题；他人会话与已删除会话一律 404（不泄漏存在性）",
)


# ══════════════════════════════════════════════════════════════════════
#  生成：#14 问答 → #10 历史 → #15 查询结果 → #14 stream=true 拒绝
# ══════════════════════════════════════════════════════════════════════

section("用例 #6：#14 发送问题并生成回答（预期 200，status=completed，带引用与来源快照）")
run_case(
    "06_completion",
    "POST",
    f"{CHAT_PATH}/completions",
    "06_chat_completion.json",
    purpose=(
        "检索 → 生成 → 落库 → 结果台账一次完成；answer 依赖模型可用性——"
        "模型不可用回 500 INTERNAL_ERROR 且台账记 failed，#15 会原样反映"
    ),
)

# 幂等重放：两份请求文件除 `authorization` 的运行时时间戳外逐字相同，
# 第二次必须拿回首次回执（X-Idempotent-Replay: true），而不是 409 或重新生成。
section("用例 #6-1：#14 幂等首调（专用 request_id = req-chat-replay-001）")
run_case(
    "06_completion_replay_first",
    "POST",
    f"{CHAT_PATH}/completions",
    "06_chat_completion_replay_first.json",
    purpose="首次受理：铺场给下一条重放用例；脚本跑第二遍时本用例本身就是回放",
)

section("用例 #6-2：#14 幂等重放（同 request_id、仅授权时间戳刷新 → 200 回放首次回执）")
run_case(
    "06_completion_replay_again",
    "POST",
    f"{CHAT_PATH}/completions",
    "06_chat_completion_replay_again.json",
    purpose=(
        "authorization.issued_at / expires_at 是调用时刻，不计入幂等正文摘要；"
        "必须回放同一 assistant_message_id（重新生成或 409 都算契约缺陷）"
    ),
)

section("用例 #7：#10 会话详情含历史（预期 200，user / assistant 逐条成对）")
run_case(
    "07_session_detail_history",
    "POST",
    f"{SESSIONS_PATH}/__SESSION_ID__/query",
    "06_chat_session_detail_history.json",
    purpose="历史逐条按当前知识授权判定可见性：助手消息必须带齐 citations 与 source_snapshots",
)

section(f"用例 #8：#15 查询原操作结果（path = {COMPLETION_REQUEST_ID}，预期 200）")
run_case(
    "08_request_result",
    "POST",
    f"{CHAT_PATH}/requests/{COMPLETION_REQUEST_ID}/query",
    "06_chat_request_result.json",
    purpose="台账只存回执（不含正文），answer 由 assistant_message_id 回查消息表；清空/删权后 result_visible=false",
)

section("用例 #9：#14 stream=true（预期 400 UNSUPPORTED_STREAM）")
run_case(
    "09_completion_stream",
    "POST",
    f"{CHAT_PATH}/completions",
    "06_chat_completion_stream.json",
    purpose="本期非流式：在进入检索与模型调用之前就拒绝，避免昂贵调用后才发现参数不支持",
)


# ══════════════════════════════════════════════════════════════════════
#  破坏性用例（只作用于临时会话）：#13 清空 → #12 删除 → #10 404
# ══════════════════════════════════════════════════════════════════════

section("用例 #10：#13 清空临时会话历史（预期 200，cleared=true）")
run_case(
    "10_session_clear",
    "DELETE",
    f"{SESSIONS_PATH}/__TEMP_SESSION_ID__/messages",
    "06_chat_session_clear.json",
    purpose="清空保留会话壳；此后迟到生成结果直接丢弃（临时会话无消息，本例验证回执与幂等域）",
)

section("用例 #11：#12 删除临时会话（预期 200，deleted=true）")
run_case(
    "11_session_delete",
    "DELETE",
    f"{SESSIONS_PATH}/__TEMP_SESSION_ID__",
    "06_chat_session_delete.json",
    purpose="DELETE 带 JSON 正文；事件域幂等并留墓碑，防止删除后重发同一 ID 把内容复活",
)

section("用例 #12：#10 查询已删除会话（预期 404 NOT_FOUND）")
run_case(
    "12_session_detail_deleted",
    "POST",
    f"{SESSIONS_PATH}/__TEMP_SESSION_ID__/query",
    "06_chat_session_detail_deleted.json",
    purpose="不存在 / 不属于本人 / 已删除三种情况对调用方是同一件事：一律 404，不泄漏存在性",
)


# ══════════════════════════════════════════════════════════════════════
#  异常场景：授权块必填、user_id 非法、查询未知操作
# ══════════════════════════════════════════════════════════════════════

section("用例 #13：#09 缺少 authorization 块（预期 400 INVALID_REQUEST）")
run_case(
    "13_session_list_missing_auth",
    "POST",
    f"{SESSIONS_PATH}/query",
    "06_chat_session_list_missing_auth.json",
    purpose="#09 手册标注授权块必填：缺块必须报参数错误，不能按「无授权即 0 命中」放行",
)

section("用例 #14：#08 user_id 非数字（预期 400 INVALID_REQUEST）")
run_case(
    "14_session_create_bad_user",
    "POST",
    SESSIONS_PATH,
    "06_chat_session_create_bad_user.json",
    purpose="契约 user_id 是数字主键的字符串形式；猜一个「差不多的用户」做权限下推等于给出别人的知识",
)

section("用例 #15：#15 查询未知操作（预期 404 NOT_FOUND）")
run_case(
    "15_request_result_unknown",
    "POST",
    f"{CHAT_PATH}/requests/req-chat-unknown-999/query",
    "06_chat_request_result_not_found.json",
    purpose="台账查不到就是 404；超过可查询期限是 409 RESULT_EXPIRED，两者不能混用",
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
    status = data.get("response_status")
    body = data.get("response_body") or {}
    if status not in (200, 201):
        return "-"
    if file_name in ("06_chat_session_create.json", "06_chat_session_create_temp.json"):
        return "ok" if body.get("session_id") and body.get("title") else "FAIL"
    if file_name == "06_chat_session_list.json":
        return "ok" if isinstance(body.get("sessions"), list) else "FAIL"
    if file_name == "06_chat_session_detail.json":
        ok = body.get("session_id") and "messages" not in body
        return "ok" if ok else "FAIL"
    if file_name == "06_chat_session_detail_history.json":
        roles = {item.get("role") for item in body.get("messages") or []}
        return "ok" if roles == {"user", "assistant"} else "FAIL"
    if file_name == "06_chat_completion.json":
        ok = (
            body.get("status") == "completed"
            and body.get("user_message_id")
            and body.get("assistant_message_id")
            and body.get("no_evidence") is not None
        )
        return "ok" if ok else "FAIL"
    if file_name == "06_chat_completion_replay_again.json":
        # 回放必须命中：message_id 与首次一致，且服务端确实回了 X-Idempotent-Replay
        first = (RESULTS.get("06_chat_completion_replay_first.json") or {}).get("response_body") or {}
        same_message = bool(body.get("assistant_message_id")) and (
            body.get("assistant_message_id") == first.get("assistant_message_id")
        )
        return "ok" if same_message and data.get("idempotent_replay") else "FAIL"
    if file_name == "06_chat_request_result.json":
        ok = body.get("operation") == "completion" and body.get("status") in {"completed", "failed", "pending", "cancelled"}
        return "ok" if ok else "FAIL"
    if file_name == "06_chat_session_clear.json":
        return "ok" if body.get("cleared") is True else "FAIL"
    if file_name == "06_chat_session_delete.json":
        return "ok" if body.get("deleted") is True else "FAIL"
    return "-"


section("测试结果汇总")
for file_name, data in RESULTS.items():
    body = data.get("response_body", {})
    error_code = body.get("error", {}).get("code", "-")
    if error_code != "-":
        detail = f"{error_code}: {body['error'].get('message', '')}"
    else:
        detail = f"session_id={body.get('session_id', '-')}"
    print(
        f"  {file_name:46} HTTP {data.get('response_status')} | "
        f"consistency={consistency(file_name, data):4} | {detail}"
    )
