"""M1 验收脚本（P1 门禁）：Mock GSI 用五头签名打通 20 条 `/v1` 路由，并核对审计与幂等台账。

用法（在 `backend/` 目录、服务已启动后执行）：

    rem 只打印结果
    .venv\\Scripts\\python.exe -m app.scripts.contract.verify_m1 --base-url http://127.0.0.1:8002

    rem 同时把验收记录写成 Markdown（M1 交付物）
    .venv\\Scripts\\python.exe -m app.scripts.contract.verify_m1 --base-url http://127.0.0.1:8002 ^
        --record "..\\00-document\\04-服务大脑AI项目开发过程文档\\02-P1接入层与审计验收\\01-M1验收记录.md"

复查的正是 M1 的三条验收标志：

1. **五头签名 / 防重放 / 错误码**：缺头、签名不符、时间戳越窗、nonce 重放、`authorization`
   跨度超限与已过期、写操作缺幂等键 —— 逐条断言状态码与错误体 `code`；
2. **20 条路由骨架可被打通**：逐条带签名调用，断言骨架期统一响应（501 + `INTERNAL_ERROR`
   且 `retryable=false`），并核对错误体把 `request_id` 原样带回；
3. **审计扩展生效**：本轮全部 `request_id` 都要在 `sbp_ai_system_api_log` 查到，被拒请求同样留痕，
   `security_check` 六字段齐全、签名只存摘要。

另有一段**台账直连校验**：`reserve → complete → replay`、同 ID 异正文 409、墓碑 `expires_at=NULL`、
失败释放后 ID 可重用。骨架期业务未实现，501 属失败会释放预约，所以「HTTP 层幂等回放」要等
业务 Tx 落地（M2 起）才能演示，这里先用直连校验把口径钉住，避免 M1 门禁漏掉幂等。

退出码非 0 表示有验收项不达标，可直接当回归脚本用。
"""

import argparse
import asyncio
import json
import logging
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Final

import httpx

from app.config.setting import settings
from app.core.security_signature import build_signed_headers, now_ms
from app.modules_api.contract.errors import (
    IDEMPOTENCY_CONFLICT,
    INTERNAL_ERROR,
    INVALID_REQUEST,
    UNAUTHORIZED,
    ContractApiError,
)
from app.modules_api.contract.idempotency import (
    SCOPE_EVENT,
    SCOPE_ID_KEYS,
    SCOPE_JOB,
    SCOPE_REQUEST,
    complete,
    release,
    reserve,
)
from app.utils.common_util import canonical_json_sha256

from .testing import SKELETON_STATUS, json_dict

# (编号, 方法, 路径, 幂等域)。幂等域为 None 的接口不接幂等依赖；编号与手册 §五 一致
ROUTES: Final[tuple[tuple[str, str, str, str | None], ...]] = (
    ("#01", "POST", "/v1/parse/jobs", SCOPE_JOB),
    ("#02", "GET", "/v1/parse/jobs/job-m1", None),
    ("#03", "POST", "/v1/knowledge/entries", SCOPE_REQUEST),
    ("#04", "POST", "/v1/knowledge/entries/pub-m1/query", None),
    ("#05", "PUT", "/v1/knowledge/entries/pub-m1", SCOPE_REQUEST),
    ("#06", "DELETE", "/v1/knowledge/entries/pub-m1", SCOPE_REQUEST),
    ("#07", "POST", "/v1/knowledge/search", None),
    ("#08", "POST", "/v1/chat/sessions", SCOPE_REQUEST),
    ("#09", "POST", "/v1/chat/sessions/query", None),
    ("#10", "POST", "/v1/chat/sessions/sess-m1/query", None),
    ("#11", "PATCH", "/v1/chat/sessions/sess-m1", SCOPE_REQUEST),
    ("#12", "DELETE", "/v1/chat/sessions/sess-m1", SCOPE_EVENT),
    ("#13", "DELETE", "/v1/chat/sessions/sess-m1/messages", SCOPE_EVENT),
    ("#14", "POST", "/v1/chat/completions", SCOPE_REQUEST),
    ("#15", "POST", "/v1/chat/requests/req-m1/query", None),
    ("#16", "POST", "/v1/knowledge/duplicates/check", None),
    ("#17", "POST", "/v1/knowledge/duplicates/groups/query", None),
    ("#18", "POST", "/v1/knowledge/duplicates/groups/grp-m1/query", None),
    ("#19", "POST", "/v1/knowledge/duplicates/merge-preview", None),
    ("#20", "POST", "/v1/knowledge/entries/pub-m1/deactivate", SCOPE_REQUEST),
)

GROUP_ROUTE: Final[str] = "路由骨架（20 条）"
GROUP_SECURITY: Final[str] = "签名与授权"
GROUP_AUDIT: Final[str] = "审计留痕"
GROUP_LEDGER: Final[str] = "幂等台账"


@dataclass
class Case:
    """一条验收项：`actual` 与 `expected` 都留成字符串，表格里直观可比。"""

    group: str
    name: str
    expected: str
    actual: str
    passed: bool
    note: str = ""


@dataclass
class Gate:
    """HTTP 侧验收：签名由 `build_signed_headers` 生成，与服务端同源（不是手搓 hex）。"""

    base_url: str
    secret: str
    client_id: str
    batch: str
    client: httpx.Client = field(init=False)
    cases: list[Case] = field(default_factory=list)
    # 本轮发出去的请求（允许 request_id 重复：nonce 重放用例的两条请求共用一个正文）
    sent: list[tuple[str, int]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.client = httpx.Client(base_url=self.base_url, timeout=15.0)

    def _request_id(self, tag: str) -> str:
        """每轮 request_id 带批次号：既方便在审计表里定位，也避免与历史轮次的幂等台账撞键。"""
        return f"{self.batch}-{tag}"

    # ── 请求与断言 ──────────────────────────────────────────────────

    def _raw_body(self, payload: dict[str, Any] | None) -> bytes:
        return b"" if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def call(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        sign: bool = True,
        secret: str | None = None,
        timestamp_ms: int | None = None,
        nonce: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """发一条请求。`sign=False` 用于「缺签名头」，`nonce=` 复用以构造重放。"""
        body = self._raw_body(payload)
        merged = dict(headers or {})
        if sign:
            merged.update(
                build_signed_headers(
                    secret if secret is not None else self.secret,
                    client_id=self.client_id,
                    method=method,
                    path=path,
                    body=body,
                    timestamp_ms=timestamp_ms,
                    nonce=nonce,
                )
            )
        if body:
            merged["Content-Type"] = "application/json"
        return self.client.request(method, path, content=body or None, headers=merged)

    def check(
        self,
        group: str,
        name: str,
        response: httpx.Response,
        *,
        status: int,
        code: str,
        request_id: str | None = None,
        note: str = "",
    ) -> None:
        """断言状态码 + 错误体三段式；`request_id` 传了就必须原样回带。"""
        payload = json_dict(response)
        error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        actual_code = error.get("code")
        retryable = error.get("retryable")

        problems: list[str] = []
        if response.status_code != status:
            problems.append(f"HTTP {response.status_code}≠{status}")
        if actual_code != code:
            problems.append(f"code={actual_code}≠{code}")
        if not isinstance(retryable, bool):
            problems.append("缺少布尔 retryable")
        if not str(error.get("message") or "").strip():
            problems.append("缺少 message")
        if request_id is not None and payload.get("request_id") != request_id:
            problems.append(f"request_id 未回带（{payload.get('request_id')}）")

        actual = f"HTTP {response.status_code} / {actual_code}"
        if isinstance(retryable, bool):
            actual += f" / retryable={str(retryable).lower()}"
        # 备注保留人工说明，同时把真实问题附在后面：否则失败原因会被说明文字盖掉
        notes = [note] if note else []
        notes.extend(problems)
        self.cases.append(
            Case(
                group=group,
                name=name,
                expected=f"HTTP {status} / {code}",
                actual=actual,
                passed=not problems,
                note="；".join(notes),
            )
        )
        if request_id:
            self.sent.append((request_id, response.status_code))

    # ── 三段验收 ────────────────────────────────────────────────────

    def run_routes(self) -> None:
        """20 条路由逐条打通：签名 → 审计 → （写操作）幂等 → 501 骨架占位。"""
        for number, method, path, scope in ROUTES:
            request_id = self._request_id(number)
            if method == "GET":
                # #02 是轮询接口，不带正文；request_id 也就没有回归对象
                payload = None
                expect_id = None
            else:
                payload = {"request_id": request_id, "tenant_id": settings.CONTRACT_TENANT_ID, "user_id": "42"}
                # request 域的幂等键就是 request_id 本身；另外两域另加一列，不能覆盖标记
                if scope and scope != SCOPE_REQUEST:
                    payload[SCOPE_ID_KEYS[scope]] = f"{request_id}-{SCOPE_ID_KEYS[scope]}"
                expect_id = request_id
            response = self.call(method, path, payload)
            self.check(
                GROUP_ROUTE,
                f"{number} {method} {path}",
                response,
                status=SKELETON_STATUS,
                code=INTERNAL_ERROR,
                request_id=expect_id,
                note="骨架期：签名/审计/幂等已生效，业务待 Tx" if response.status_code == SKELETON_STATUS else "",
            )

    def run_security(self) -> None:
        """签名与授权的六类拒绝口径（T1.1）。

        每条用例用独立的 request_id：审计是按 request_id 核对的，共用标记会把多条请求
        算成一条（nonce 首用/重放例外，它们必须发同一个正文才能复用同一组签名头）。
        """
        path = "/v1/knowledge/search"
        unsigned_id = self._request_id("#07u")
        unsigned = self.call("POST", path, {"request_id": unsigned_id}, sign=False)
        self.check(
            GROUP_SECURITY,
            "缺少全部签名头",
            unsigned,
            status=401,
            code=UNAUTHORIZED,
            request_id=unsigned_id,
            note="带 WWW-Authenticate" if unsigned.headers.get("WWW-Authenticate") else "缺 WWW-Authenticate",
        )
        if not unsigned.headers.get("WWW-Authenticate"):
            self.cases[-1].passed = False

        wrong_id = self._request_id("#07w")
        self.check(
            GROUP_SECURITY,
            "签名不符（密钥不匹配）",
            self.call("POST", path, {"request_id": wrong_id}, secret="wrong-secret"),
            status=401,
            code=UNAUTHORIZED,
            request_id=wrong_id,
        )

        skew_id = self._request_id("#07t")
        self.check(
            GROUP_SECURITY,
            "时间戳越窗（-6 分钟）",
            self.call(
                "POST",
                path,
                {"request_id": skew_id},
                timestamp_ms=now_ms() - settings.CONTRACT_CLOCK_SKEW_MS - 60_000,
            ),
            status=401,
            code=UNAUTHORIZED,
            request_id=skew_id,
            note=f"窗口 ±{settings.CONTRACT_CLOCK_SKEW_MS // 1000}s",
        )

        # nonce 复用：先合法打一次，再用同一组头重放（正文必须一致，否则先撞签名不符）
        replay_id = self._request_id("#07r")
        replay_payload = {"request_id": replay_id}
        nonce = f"m1-replay-{uuid.uuid4().hex[:16]}"
        self.check(
            GROUP_SECURITY,
            "nonce 首用（合法）",
            self.call("POST", path, replay_payload, nonce=nonce),
            status=SKELETON_STATUS,
            code=INTERNAL_ERROR,
            request_id=replay_id,
        )
        self.check(
            GROUP_SECURITY,
            "nonce 重放（同头第二次）",
            self.call("POST", path, replay_payload, nonce=nonce),
            status=401,
            code=UNAUTHORIZED,
            request_id=replay_id,
            note="nonce 已落库，重放被拒",
        )

        stamp = now_ms()
        over_id = self._request_id("#07a")
        over_ttl = {
            "request_id": over_id,
            "authorization": {"scope_id": "scope-m1", "issued_at": stamp, "expires_at": stamp + 300_000},
        }
        self.check(
            GROUP_SECURITY,
            f"authorization 跨度超限（300s＞{settings.CONTRACT_AUTHORIZATION_MAX_TTL_MS // 1000}s）",
            self.call("POST", path, over_ttl),
            status=400,
            code=INVALID_REQUEST,
            request_id=over_id,
        )

        expired_id = self._request_id("#07b")
        expired = {
            "request_id": expired_id,
            "authorization": {"scope_id": "scope-m1", "issued_at": stamp - 60_000, "expires_at": stamp - 1},
        }
        self.check(
            GROUP_SECURITY,
            "authorization 已过期",
            self.call("POST", path, expired),
            status=401,
            code=UNAUTHORIZED,
            request_id=expired_id,
            note="凭据失效语义，要求重签",
        )

        missing_id = self._request_id("#01n")
        missing_key = {"request_id": missing_id, "tenant_id": settings.CONTRACT_TENANT_ID}
        self.check(
            GROUP_SECURITY,
            "写操作缺幂等键（#01 无 job_id）",
            self.call("POST", "/v1/parse/jobs", missing_key),
            status=400,
            code=INVALID_REQUEST,
            request_id=missing_id,
            note="缺 job_id 直接 400，不落台账",
        )

    def close(self) -> None:
        self.client.close()


# ── 审计与台账（需要数据库） ─────────────────────────────────────────


async def audit_cases(gate: Gate) -> list[Case]:
    """核对本轮请求是否全部留痕，以及 `security_check` 的结构与取值。"""
    from sqlalchemy import select

    from app.core.database import async_session_factory
    from app.modules.system.api_log.model import SystemApiLogModel

    request_ids = sorted({rid for rid, _ in gate.sent})
    expected_rows = len(gate.sent)
    rows: list[SystemApiLogModel] = []
    deadline = time.monotonic() + 8.0
    while True:
        async with async_session_factory() as session:
            rows = list(
                (
                    await session.execute(
                        select(SystemApiLogModel).where(SystemApiLogModel.request_id.in_(request_ids))
                    )
                )
                .scalars()
                .all()
            )
        if len(rows) >= expected_rows or time.monotonic() > deadline:
            break
        await asyncio.sleep(0.25)  # 审计是响应后异步落库，需要等后台任务写完

    cases: list[Case] = []
    found = {row.request_id for row in rows}
    missing = [rid for rid in request_ids if rid not in found]
    cases.append(
        Case(
            GROUP_AUDIT,
            "本轮请求全部留痕",
            f"{expected_rows} 条",
            f"{len(rows)} 条",
            not missing and len(rows) == expected_rows,
            note=f"缺：{'、'.join(missing[:3])}" if missing else "含 400/401 被拒请求；#02 GET 无请求 ID，按路径留痕",
        )
    )

    rejected = [(rid, status) for rid, status in gate.sent if status >= 400 and status != SKELETON_STATUS]
    # 同一 request_id 可能对应多条行（nonce 首用/重放），所以按「ID + 响应码」逐对核对
    rejected_logged = [
        (rid, status)
        for rid, status in rejected
        if any(
            row.request_id == rid and row.response_code == status and (row.security_check or {}).get("failure_code")
            for row in rows
        )
    ]
    cases.append(
        Case(
            GROUP_AUDIT,
            "被拒请求留痕且带失败原因",
            f"{len(rejected)} 条（带 failure_code）",
            f"{len(rejected_logged)} 条",
            bool(rejected) and len(rejected_logged) == len(rejected),
            note="401 记 signature_result=failed / 其他 4xx 记参数类 failure_code",
        )
    )

    required = {
        "headers_present",
        "timestamp_deviation_ms",
        "nonce_state",
        "signature_result",
        "signature_digest",
        "failure_code",
    }
    incomplete = [row.request_id for row in rows if not required <= set(row.security_check or {})]
    cases.append(
        Case(
            GROUP_AUDIT,
            "security_check 六字段齐全",
            "6/6",
            f"{len(rows) - len(incomplete)}/{len(rows)}",
            not incomplete,
            note="headers_present/timestamp_deviation_ms/nonce_state/signature_result/signature_digest/failure_code",
        )
    )

    passed_rows = [row for row in rows if (row.security_check or {}).get("signature_result") == "passed"]
    digest_ok = bool(passed_rows) and all(
        len(str((row.security_check or {}).get("signature_digest") or "")) == 12 for row in passed_rows
    )
    reused = [row for row in rows if (row.security_check or {}).get("nonce_state") == "reused"]
    cases.append(
        Case(
            GROUP_AUDIT,
            "签名只存摘要（前 8+后 4）",
            "12 位",
            f"{len(passed_rows)} 条通过验签",
            digest_ok,
            note="不回写 64 位签名原文",
        )
    )
    cases.append(
        Case(
            GROUP_AUDIT,
            "nonce 状态可区分首用/重放",
            "first_use + reused",
            f"reused {len(reused)} 条",
            bool(reused),
            note="重放请求的 nonce_state=reused",
        )
    )
    return cases


async def ledger_cases(run: str) -> list[Case]:
    """台账直连校验（骨架期 HTTP 层演示不了回放：501 属失败必然释放预约）。"""
    from sqlalchemy import delete, select

    from app.core.database import async_session_factory
    from app.modules.system.models import SystemIdempotencyModel

    biz_id = f"ledger-{run}-request"
    other_biz = f"ledger-{run}-released"
    first_hash = canonical_json_sha256({"request_id": biz_id, "value": 1})
    second_hash = canonical_json_sha256({"request_id": biz_id, "value": 2})
    cases: list[Case] = []

    reservation = await reserve(scope=SCOPE_REQUEST, biz_id=biz_id, body_hash=first_hash)
    cases.append(
        Case(
            GROUP_LEDGER,
            "首用预约 state=new",
            "new",
            reservation.state,
            reservation.state == "new" and not reservation.is_replay,
        )
    )

    receipt = {"status_code": 201, "body": {"request_id": biz_id, "accepted": True}}
    await complete(scope=SCOPE_REQUEST, biz_id=biz_id, body_hash=first_hash, result=receipt)
    replay = await reserve(scope=SCOPE_REQUEST, biz_id=biz_id, body_hash=first_hash)
    replay_ok = replay.is_replay and (replay.result or {}).get("body") == receipt["body"]
    cases.append(
        Case(
            GROUP_LEDGER,
            "同 ID 同正文回放首次回执",
            "state=replay + 回执一致",
            f"state={replay.state}",
            replay_ok,
        )
    )

    conflict_ok, conflict_actual = False, "-"
    try:
        await reserve(scope=SCOPE_REQUEST, biz_id=biz_id, body_hash=second_hash)
    except ContractApiError as exc:
        conflict_ok = (exc.code, exc.status_code) == (IDEMPOTENCY_CONFLICT, 409)
        conflict_actual = f"HTTP {exc.status_code} / {exc.code}"
    cases.append(
        Case(
            GROUP_LEDGER,
            "同 ID 异正文 → 409",
            "HTTP 409 / IDEMPOTENCY_CONFLICT",
            conflict_actual,
            conflict_ok,
            note="正文变了必须换 ID，不允许悄悄覆盖",
        )
    )

    await complete(scope=SCOPE_REQUEST, biz_id=biz_id, body_hash=first_hash, result=receipt, tombstone=True)
    async with async_session_factory() as session:
        row = (
            await session.execute(
                select(SystemIdempotencyModel).where(
                    SystemIdempotencyModel.scope == SCOPE_REQUEST,
                    SystemIdempotencyModel.biz_id == biz_id,
                )
            )
        ).scalar_one()
    cases.append(
        Case(
            GROUP_LEDGER,
            "删除类墓碑永不清理",
            "status=tombstone / expires_at=NULL",
            f"status={row.status} / expires_at={row.expires_at}",
            row.status == "tombstone" and row.expires_at is None,
            note="否则「删除 → 重发同 ID」会让知识复活",
        )
    )

    await release(scope=SCOPE_REQUEST, biz_id=other_biz, body_hash=first_hash)
    await reserve(scope=SCOPE_REQUEST, biz_id=other_biz, body_hash=first_hash)
    await release(scope=SCOPE_REQUEST, biz_id=other_biz, body_hash=first_hash)
    again = await reserve(scope=SCOPE_REQUEST, biz_id=other_biz, body_hash=first_hash)
    cases.append(
        Case(
            GROUP_LEDGER,
            "失败释放后同 ID 可重试",
            "state=new",
            again.state,
            again.state == "new",
            note="不释放会把 ID 永久锁在 processing",
        )
    )

    async with async_session_factory() as session:
        await session.execute(
            delete(SystemIdempotencyModel).where(SystemIdempotencyModel.biz_id.in_([biz_id, other_biz]))
        )
        await session.commit()
    return cases


# ── 输出 ──────────────────────────────────────────────────────────


def render(cases: list[Case], *, base_url: str, client_id: str) -> str:
    passed = sum(1 for case in cases if case.passed)
    failed = [case for case in cases if not case.passed]
    grouped: dict[str, list[Case]] = defaultdict(list)
    for case in cases:
        grouped[case.group].append(case)

    lines = [
        f"# M1 验收记录（{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}）",
        "",
        "> 由 `python -m app.scripts.contract.verify_m1 --record` 生成；表格即本次运行的原始输出，手写补充见 README。",
        "",
        f"- 服务地址：`{base_url}`",
        f"- client_id：`{client_id}`（自签测试密钥 `CONTRACT_V1_SHARED_SECRET`）",
        f"- 验收项：{len(cases)} 条，通过 {passed} 条，失败 {len(failed)} 条",
        "",
    ]
    for group in (GROUP_ROUTE, GROUP_SECURITY, GROUP_AUDIT, GROUP_LEDGER):
        items = grouped.get(group)
        if not items:
            continue
        lines.extend(
            [
                f"## {group}（{sum(1 for item in items if item.passed)}/{len(items)}）",
                "",
                "| # | 验收项 | 期望 | 实际 | 结论 | 备注 |",
                "| --- | --- | --- | --- | --- | --- |",
            ]
        )
        for index, item in enumerate(items, start=1):
            flag = "通过" if item.passed else "**失败**"
            lines.append(f"| {index} | {item.name} | {item.expected} | {item.actual} | {flag} | {item.note} |")
        lines.append("")
    if failed:
        lines.extend(["## 失败明细", ""])
        lines.extend(f"- {case.group} / {case.name}：期望 {case.expected}，实际 {case.actual}；{case.note}" for case in failed)
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="M1 验收：Mock GSI 签名打通 20 条 /v1 路由 + 审计/幂等核对")
    parser.add_argument("--base-url", default="http://127.0.0.1:8002", help="AI 服务地址")
    parser.add_argument("--client-id", default=settings.CONTRACT_V1_CLIENT_ID, help="签名客户端标识")
    parser.add_argument("--secret", default=settings.CONTRACT_V1_SHARED_SECRET, help="HMAC 共享密钥")
    parser.add_argument("--batch", default="", help="批次号（默认按时间生成），用于隔离请求 ID")
    parser.add_argument("--skip-db", action="store_true", help="跳过审计与台账核对（无库环境只验 HTTP）")
    parser.add_argument("--record", default="", help="验收记录输出路径（Markdown），留空只打印")
    args = parser.parse_args()

    # HTTP/ORM 的 DEBUG 洪水会把验收结论埋掉
    for name in ("httpx", "httpcore", "sqlalchemy"):
        logging.getLogger(name).setLevel(logging.WARNING)
    for name in list(logging.root.manager.loggerDict):
        if isinstance(name, str) and name.startswith(("httpx", "httpcore", "sqlalchemy")):
            logging.getLogger(name).setLevel(logging.WARNING)

    if not args.secret:
        print("[FAIL] CONTRACT_V1_SHARED_SECRET 为空：服务端会一律返回 503，先配置密钥再验收")
        return 1

    batch = args.batch or f"m1-{datetime.now().strftime('%m%d%H%M%S')}"
    gate = Gate(base_url=args.base_url, secret=args.secret, client_id=args.client_id, batch=batch)

    print(f"[INFO] 批次 {batch}，目标 {args.base_url}，client_id={args.client_id}")
    try:
        gate.run_routes()
        gate.run_security()
    finally:
        gate.close()

    cases = list(gate.cases)
    if args.skip_db:
        print("[WARN] --skip-db：审计留痕与幂等台账未核对，M1 门禁不能只靠这一档")
    else:
        cases.extend(asyncio.run(_db_checks(gate)))

    report = render(cases, base_url=args.base_url, client_id=args.client_id)
    if args.record:
        target = Path(args.record)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(report, encoding="utf-8")
        print(f"[INFO] 验收记录已写入：{target}")
    else:
        print("\n" + report)

    failed = [case for case in cases if not case.passed]
    print(f"[INFO] 验收项 {len(cases)} 条，通过 {len(cases) - len(failed)} 条，失败 {len(failed)} 条")
    return 1 if failed else 0


async def _db_checks(gate: Gate) -> list[Case]:
    """审计 + 台账一次性跑完再释放连接池（跨事件循环复用 asyncpg 连接会报错）。"""
    from app.core.database import async_engine

    try:
        cases = await audit_cases(gate)
        cases.extend(await ledger_cases(gate.batch))
    finally:
        await async_engine.dispose()
    return cases


if __name__ == "__main__":
    raise SystemExit(main())
