"""Mock GSI 调用脚本：`/v1` 契约冒烟驱动器（阶段 0 后由 `app/poc/mock_client.py` 迁移而来）。

用法（在 `backend/` 目录、服务已启动后执行）：

    rem 单跑：只打用例结果
    .venv\\Scripts\\python.exe -m app.scripts.contract.mock_gsi --base-url http://127.0.0.1:8003

    rem 骨架期：业务接口还是 501 占位，用 --allow-skeleton 把 501 记为「未实现」而不是失败
    .venv\\Scripts\\python.exe -m app.scripts.contract.mock_gsi --base-url http://127.0.0.1:8003 --allow-skeleton

    rem 输出冒烟记录
    .venv\\Scripts\\python.exe -m app.scripts.contract.mock_gsi --base-url http://127.0.0.1:8003 --allow-skeleton ^
        --record "..\\00-document\\04-服务大脑AI项目开发过程文档\\01-POC验证\\01-POC冒烟记录.md"

它做的事：
1. 起一个本地 `http.server` 托管样例 Markdown，给 #01 一个**真实可拉取**的 `file_url`
   （契约里 file_url 必须是 AI 能访问的地址，用假链接测不出拉取链路的真实缺陷）；
2. 用与服务端**同一份** `build_signed_headers` 构造签名，逐条打契约用例：签名失败四类、
   #01 幂等、#02 轮询与 404、#03 幂等与 409、#07 关键词与自然语言检索、
   #08–#15 会话与问答（建会话 → 生成 → 历史 / 结果查询 → 改名 → 清空 → 删除）；
3. `--record` 输出 Markdown 冒烟记录。

退出码非 0 表示有用例不符合预期 —— 便于在联调期直接把它当回归脚本用。

与 `verify_m1` 的分工（见 `03-POC下线与契约迁移方案.md` §五 风险 7）：
- `verify_m1` 保「M1 门禁 + `--record` 证据」；
- 本脚本保「GSI 侧样例 + 负向用例 + 联调」。

刻意简化：不覆盖修订 / 重复组 / 停用，也不做 schema 全量校验；断言只看「契约字段是否按
口径出现」。

`--user-id` 必须是**数字主键的字符串形式**：`/v1` 把 `user_id` 当下推权限的输入，
传一个非数字值不会报错，只会「什么都检索不到」——冒烟结果看着全过、实则一条证据都没有。
"""

import argparse
import json
import logging
import threading
import time
from datetime import datetime
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Final

import httpx

from app.config.setting import settings
from app.core.security_signature import build_signed_headers
from app.modules.kb.chat.schema import REASON_SOURCE_UNAVAILABLE
from app.modules_api.contract.errors import (
    INVALID_REQUEST,
    NOT_FOUND,
    STALE_REVISION,
    UNAUTHORIZED,
    UNSUPPORTED_STREAM,
)
from app.modules_api.contract.knowledge.schema import canonical_content_hash

from .testing import SKELETON_STATUS, error_code, json_dict

SAMPLE_DOC: Final[str] = """# VPN 申请操作手册

## 申请流程

在服务台提交 vpn 申请，由负责人审核后开通账号。提交时需填写使用期限与访问网段。

## 审批时限

普通申请 1 个工作日内完成审核，涉及生产网段的申请需额外由安全组复核。

## 常见问题

申请被驳回时可在服务台查看驳回原因，修改后重新提交即可。
"""

# #03 的切片集合：由 GSI 侧生成 chunk_id（AI 只负责存与向量化），这里手工构造三条
CHUNKS: Final[list[dict[str, Any]]] = [
    {
        "chunk_id": "chunk-1",
        "seq": 1,
        "heading": "申请流程",
        "content": "在服务台提交 vpn 申请，由负责人审核后开通账号。提交时需填写使用期限与访问网段。",
        "page_from": 1,
        "page_to": 1,
    },
    {
        "chunk_id": "chunk-2",
        "seq": 2,
        "heading": "审批时限",
        "content": "普通申请 1 个工作日内完成审核，涉及生产网段的申请需额外由安全组复核。",
        "page_from": 1,
        "page_to": 1,
    },
    {
        "chunk_id": "chunk-3",
        "seq": 3,
        "heading": "常见问题",
        "content": "申请被驳回时可在服务台查看驳回原因，修改后重新提交即可。",
        "page_from": 1,
        "page_to": 1,
    },
]

CHAT_PROMPT: Final[str] = "你是企业知识库问答助手，只能依据【参考资料】回答问题，资料未覆盖时直接说明。"


class _QuietHandler(SimpleHTTPRequestHandler):
    """静默静态文件服务：不让 http.server 的访问日志盖住用例输出。"""

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - 父类签名固定
        return


class FileServer:
    """临时目录 + 本地 HTTP 服务，给 #01 一个可拉取的 file_url。"""

    def __init__(self) -> None:
        self._tmp = TemporaryDirectory(prefix="contract-file-")
        root = Path(self._tmp.name)
        (root / "vpn-guide.md").write_text(SAMPLE_DOC, encoding="utf-8")
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), partial(_QuietHandler, directory=str(root)))
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> "FileServer":
        self._thread.start()
        return self

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/vpn-guide.md"

    def __exit__(self, *exc_info: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
        self._tmp.cleanup()


class Smoke:
    """按契约顺序逐条打用例，结果累积到 `rows`（供 Markdown 记录使用）。"""

    def __init__(
        self,
        base_url: str,
        *,
        secret: str,
        client_id: str,
        user_id: str,
        with_chat: bool,
        allow_skeleton: bool,
    ) -> None:
        self.base_url = base_url
        self.secret = secret
        self.client_id = client_id
        self.user_id = user_id
        self.with_chat = with_chat
        self.allow_skeleton = allow_skeleton
        self.rows: list[dict[str, Any]] = []
        self._client = httpx.Client(base_url=base_url, timeout=180.0, trust_env=False)
        self.run_id = f"{int(time.time())}"
        # authorization 块在整轮冒烟内固定：同一个 request_id 重试时正文必须完全一致，
        # 否则幂等会被误判成「同 ID 异正文」（契约 §七.6）
        self._auth = self._authorization()

    # ── 请求构造 ──────────────────────────────────────────────────────

    def _headers(
        self,
        method: str,
        path: str,
        raw: bytes,
        *,
        secret: str | None = None,
        timestamp_ms: int | None = None,
        nonce: str | None = None,
    ) -> dict[str, str]:
        return build_signed_headers(
            secret or self.secret,
            client_id=self.client_id,
            method=method,
            path=path,
            body=raw,
            timestamp_ms=timestamp_ms,
            nonce=nonce,
        )

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        secret: str | None = None,
        timestamp_ms: int | None = None,
        nonce: str | None = None,
    ) -> httpx.Response:
        """签名请求：DELETE / PATCH 也带正文（#12 / #13 的正文只承载身份与幂等键）。"""
        raw = b"" if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            **self._headers(method, path, raw, secret=secret, timestamp_ms=timestamp_ms, nonce=nonce),
        }
        return self._client.request(method, path, content=raw, headers=headers)

    def post(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        secret: str | None = None,
        timestamp_ms: int | None = None,
        nonce: str | None = None,
    ) -> httpx.Response:
        return self.request("POST", path, payload, secret=secret, timestamp_ms=timestamp_ms, nonce=nonce)

    def get(self, path: str, *, raw: bytes = b"") -> httpx.Response:
        return self._client.get(path, headers=self._headers("GET", path, raw))

    def record(
        self,
        name: str,
        response: httpx.Response,
        *,
        status: int,
        ok: bool,
        note: str = "",
    ) -> dict[str, Any]:
        """记一条用例：`ok` 由调用方对响应体做的契约断言决定。

        `--allow-skeleton` 下 501 表示「业务尚未落地」，记为未实现而不算失败：
        阶段 0 之后 `/v1` 只有 contract 的骨架，这份脚本要能在骨架期跑出退出码 0。
        """
        body = json_dict(response)
        skeleton = self.allow_skeleton and response.status_code == SKELETON_STATUS
        passed = skeleton or (response.status_code == status and ok)
        self.rows.append(
            {
                "name": name,
                "expected": status,
                "actual": response.status_code,
                "passed": passed,
                "skeleton": skeleton,
                "note": note,
                "request_id": body.get("request_id") or body.get("job_id") or "",
            }
        )
        flag = "SKIP" if skeleton else ("PASS" if passed else "FAIL")
        if skeleton:
            note = f"{note}（501 未实现，--allow-skeleton 跳过）".strip()
        print(f"[{flag}] {name} | HTTP {response.status_code}（期望 {status}）| {note}")
        return body

    # ── 用例 ──────────────────────────────────────────────────────────

    def run(self, file_url: str) -> None:
        self._case_signature()
        job_id = self._case_parse(file_url)
        if job_id:
            self._case_parse_job(job_id)
        publication_id = self._case_publish()
        if publication_id:
            self._case_search()
            if self.with_chat:
                self._case_chat()

    def close(self) -> None:
        self._client.close()

    def _case_signature(self) -> None:
        """签名失败四类：缺头 / 签名不符 / 时间戳越窗 / nonce 重放 —— 都必须是 401 UNAUTHORIZED。"""
        path = "/v1/knowledge/search"
        payload = self._search_payload("vpn 申请")

        response = self._client.post(path, json=payload, headers={"Content-Type": "application/json"})
        self.record("签名·缺少签名头", response, status=401, ok=error_code(json_dict(response)) == UNAUTHORIZED)

        response = self.post(path, payload, secret="wrong-secret")
        self.record("签名·签名不符", response, status=401, ok=error_code(json_dict(response)) == UNAUTHORIZED)

        response = self.post(path, payload, timestamp_ms=int(time.time() * 1000) - 600_000)
        self.record("签名·时间戳越窗", response, status=401, ok=error_code(json_dict(response)) == UNAUTHORIZED)

        nonce = f"replay-nonce-{self.run_id}"
        self.post(path, payload, nonce=nonce)
        response = self.post(path, payload, nonce=nonce)
        self.record("签名·nonce 重放", response, status=401, ok=error_code(json_dict(response)) == UNAUTHORIZED)

    def _parse_payload(self, file_url: str, job_id: str) -> dict[str, Any]:
        return {
            "job_id": job_id,
            "doc_id": "doc-demo-001",
            "version_id": "1",
            "file_url": file_url,
            "file_name": "vpn-guide.md",
            "options": {"chunk": True, "language": "zh"},
            "tenant_id": "tenant-demo",
            "resource_type": "document",
            "metadata": {"title": "VPN 申请操作手册", "doc_type": "document", "language": "zh", "trust": "a"},
        }

    def _case_parse(self, file_url: str) -> str:
        """#01：首次 202、同 ID 同正文幂等 202；返回 job_id 供 #02 轮询。"""
        job_id = f"parse-demo-{self.run_id}"
        payload = self._parse_payload(file_url, job_id)

        response = self.post("/v1/parse/jobs", payload)
        body = json_dict(response)
        self.record(
            "#01 受理解析任务",
            response,
            status=202,
            ok=body_ok(body, job_id),
            note=f"status={body.get('status')}",
        )

        response = self.post("/v1/parse/jobs", payload)
        receipt = json_dict(response)
        self.record("#01 幂等重试（同 job_id 同正文）", response, status=202, ok=receipt.get("status") == "queued")
        return job_id

    def _case_parse_job(self, job_id: str) -> None:
        """#02：轮询到终态；再查一个不存在的任务必须 404 NOT_FOUND。"""
        status = ""
        response: httpx.Response
        body: dict[str, Any] = {}
        for _ in range(60):
            response = self.get(f"/v1/parse/jobs/{job_id}")
            body = json_dict(response)
            status = str(body.get("status") or "")
            if response.status_code != 200 or status in {"succeeded", "failed"}:
                break
            time.sleep(1.0)

        result = body.get("result") or {}
        chunks = result.get("chunks") or []
        ok = status == "succeeded" and bool(chunks) and result.get("pages") == []
        self.record(
            "#02 轮询解析任务（到终态）",
            response,
            status=200,
            ok=ok,
            note=f"status={status} chunks={len(chunks)} warnings={len(result.get('warnings') or [])}",
        )

        response = self.get(f"/v1/parse/jobs/parse-not-exist-{self.run_id}")
        self.record("#02 任务不存在", response, status=404, ok=error_code(json_dict(response)) == NOT_FOUND)

    def _entry_payload(self, *, request_id: str, publication_id: str) -> dict[str, Any]:
        return {
            "request_id": request_id,
            "tenant_id": "tenant-demo",
            "user_id": self.user_id,
            "resource_type": "document",
            "resource_id": "doc-demo-001",
            "publication_id": publication_id,
            "record_id": "record-demo-001",
            "version_id": "1",
            "revision": 101,
            "knowledge_no": "kb-0000064",
            "metadata": {"title": "VPN 申请操作手册", "doc_type": "document", "language": "zh"},
            "content_hash": canonical_content_hash(CHUNKS),
            "chunks": CHUNKS,
        }

    def _case_publish(self) -> str:
        """#03：首发布 201、同 request_id 幂等 201、换 request_id 同 publication_id 必须 409。"""
        publication_id = f"pub-demo-{self.run_id}"
        payload = self._entry_payload(request_id=f"request-entry-{self.run_id}", publication_id=publication_id)

        response = self.post("/v1/knowledge/entries", payload)
        body = json_dict(response)
        self.record(
            "#03 首次发布写入",
            response,
            status=201,
            ok=body.get("accepted_chunk_count") == len(CHUNKS) and body.get("searchable") is True,
            note=f"chunks={body.get('accepted_chunk_count')} searchable={body.get('searchable')}",
        )

        response = self.post("/v1/knowledge/entries", payload)
        body = json_dict(response)
        self.record(
            "#03 幂等重试（同 request_id 同正文）",
            response,
            status=201,
            ok=body.get("accepted_chunk_count") == len(CHUNKS),
        )

        response = self.post(
            "/v1/knowledge/entries",
            self._entry_payload(request_id=f"request-entry-again-{self.run_id}", publication_id=publication_id),
        )
        self.record(
            "#03 同 publication_id 重复发布",
            response,
            status=409,
            ok=error_code(json_dict(response)) == STALE_REVISION,
            note="契约只写「已有 publication_id 返回 409」；本侧按基线冲突归口 STALE_REVISION（Q7）",
        )
        return publication_id

    def _authorization(self, *, ttl_ms: int = 120_000) -> dict[str, Any]:
        issued = int(time.time() * 1000)
        return {"scope_id": f"scope-{int(time.time())}", "issued_at": issued, "expires_at": issued + ttl_ms}

    def _search_payload(self, query: str, *, mode: str = "natural_language", limit: int = 10) -> dict[str, Any]:
        return {
            "request_id": f"request-search-{mode}-{self.run_id}",
            "query": query,
            "mode": mode,
            "limit": limit,
            "tenant_id": "tenant-demo",
            "user_id": self.user_id,
            "authorization": self._auth,
        }

    def _case_search(self) -> None:
        """#07：keyword 与 natural_language 都走通（召回受 GSI 授权链约束）。"""
        # 每组用例开头重新签发一次（窗口最长 120 秒，整轮冒烟可能跨过它）
        self._auth = self._authorization()

        response = self.post("/v1/knowledge/search", self._search_payload("vpn 申请", mode="keyword"))
        body = json_dict(response)
        hits = body.get("hits") or []
        self.record(
            "#07 关键词检索（mode=keyword）",
            response,
            status=200,
            # 只断言契约自洽：0 条 + no_evidence=true 同样合法——能否召回取决于该 user_id
            # 在 GSI 的受众团队是否覆盖了本脚本刚发布的发布单元（权限在 AI 侧只读）
            ok=body.get("returned_count") == len(hits) and body.get("no_evidence") == (not hits),
            note=f"hits={len(hits)} top_score={(hits[0].get('score') if hits else None)}；"
            "召回依赖 GSI 授权链（user_id 无受众团队时 0 条属预期）",
        )

        response = self.post(
            "/v1/knowledge/search",
            self._search_payload("如何申请 vpn", mode="natural_language"),
        )
        body = json_dict(response)
        hits = body.get("hits") or []
        self.record(
            "#07 自然语言检索（mode=natural_language）",
            response,
            status=200,
            # 同样只断言契约自洽：语义召回需 bge-m3 向量 + RRF，精排未接入时降级为融合序
            ok=body.get("returned_count") == len(hits) and body.get("no_evidence") == (not hits),
            note=f"hits={len(hits)} top_score={(hits[0].get('score') if hits else None)}；"
            "召回依赖 bge-m3 向量与 GSI 授权链（向量化不可用时为 503，属依赖故障而非无证据）",
        )

        response = self.post(
            "/v1/knowledge/search",
            {**self._search_payload("vpn", mode="keyword"), "authorization": self._authorization(ttl_ms=300_000)},
        )
        self.record(
            "#07 authorization 跨度超限",
            response,
            status=400,
            ok=error_code(json_dict(response)) == INVALID_REQUEST,
            note="契约未定义该场景专用错误码",
        )

    def _chat_payload(self, *, session_id: str, question: str, request_id: str, stream: bool = False) -> dict[str, Any]:
        return {
            "request_id": request_id,
            "tenant_id": "tenant-demo",
            "user_id": self.user_id,
            # session_id 必须先由 #08 建出来：POC 期「首轮传空串」的口径已作废
            "session_id": session_id,
            "question": question,
            "prompt_text": CHAT_PROMPT,
            "model_key": "default",
            "stream": stream,
            "authorization": self._auth,
            "options": {"language": "zh-CN", "max_output_tokens": 512},
        }

    def _identity_payload(self, request_id: str) -> dict[str, Any]:
        """#09–#13 / #15 的公共正文：只承载身份与本次授权块（会话 ID 在路径里）。"""
        return {
            "request_id": request_id,
            "tenant_id": "tenant-demo",
            "user_id": self.user_id,
            "authorization": self._auth,
        }

    def _case_chat(self) -> None:
        """#08–#15：建会话 → 生成 → 历史 / 结果查询 → 改名 → 清空 → 删除。

        只断言「契约自洽」：能否检索到证据取决于该 ``user_id`` 在 GSI 的受众团队是否覆盖
        了本脚本刚发布的发布单元（权限在 AI 侧只读），因此看的是「``no_evidence`` 与
        citations 是否一致」，而不是「一定有引用」。
        """
        self._auth = self._authorization()

        response = self.post(
            "/v1/chat/sessions",
            {**self._identity_payload(f"request-session-{self.run_id}"), "title": "冒烟会话"},
        )
        session = json_dict(response)
        session_id = str(session.get("session_id") or "")
        self.record(
            "#08 创建会话",
            response,
            status=201,
            ok=bool(session_id) and session.get("title") == "冒烟会话",
            note=f"session_id={session_id}",
        )
        if not session_id:
            # 后续用例全部挂在会话上：没有会话就停在这里，不再连累一串误导性的失败
            return

        response = self.post(
            "/v1/chat/sessions/query",
            self._identity_payload(f"request-session-list-{self.run_id}"),
        )
        body = json_dict(response)
        listed = [item.get("session_id") for item in body.get("sessions") or []]
        self.record(
            "#09 会话列表",
            response,
            status=200,
            ok=session_id in listed,
            note=f"sessions={len(listed)} next_cursor={body.get('next_cursor')}",
        )

        # stream 应在进入检索与模型调用之前就被拒：先打一次，确认不会产生任何副作用
        response = self.post(
            "/v1/chat/completions",
            self._chat_payload(
                session_id=session_id,
                question="vpn 怎么申请？",
                request_id=f"request-chat-stream-{self.run_id}",
                stream=True,
            ),
        )
        self.record(
            "#14 stream=true 明确拒绝",
            response,
            status=400,
            ok=error_code(json_dict(response)) == UNSUPPORTED_STREAM,
        )

        request_id = f"request-chat-{self.run_id}"
        payload = self._chat_payload(
            session_id=session_id,
            question="vpn 申请后多久能审批完成？",
            request_id=request_id,
        )
        response = self.post("/v1/chat/completions", payload)
        body = json_dict(response)
        citations = body.get("citations") or []
        contents = "".join(chunk["content"] for chunk in CHUNKS)
        quotes_ok = all(citation.get("quote") and citation["quote"] in contents for citation in citations)
        no_evidence = body.get("no_evidence") is True
        self.record(
            "#14 生成回答",
            response,
            status=200,
            ok=body.get("session_id") == session_id
            and no_evidence == (not citations)
            and body.get("finish_reason") == ("no_evidence" if no_evidence else "stop")
            and quotes_ok,
            note=f"citations={len(citations)} quotes_verbatim={quotes_ok} "
            f"no_evidence={no_evidence} finish_reason={body.get('finish_reason')}",
        )
        assistant_message_id = str(body.get("assistant_message_id") or "")

        # 同 request_id 同正文重试必须回放首次结果：正文要逐字节一致，授权块因此不能重签
        response = self.post("/v1/chat/completions", payload)
        self.record(
            "#14 幂等重试（同 request_id 同正文）",
            response,
            status=200,
            ok=assistant_message_id and json_dict(response).get("assistant_message_id") == assistant_message_id,
        )

        # 一次生成可能耗时数十秒，后续用例重签授权块（窗口最长 120 秒，每个 request_id 都不同）
        self._auth = self._authorization()

        response = self.post(
            f"/v1/chat/requests/{request_id}/query",
            self._identity_payload(f"request-result-{self.run_id}"),
        )
        body = json_dict(response)
        result = body.get("result") or {}
        self.record(
            "#15 查询生成结果",
            response,
            status=200,
            ok=body.get("operation") == "completion"
            and body.get("status") == "completed"
            and body.get("result_visible") is True
            and result.get("assistant_message_id") == assistant_message_id
            and bool(result.get("answer")),
            note=f"status={body.get('status')} result_visible={body.get('result_visible')} "
            f"answer_len={len(result.get('answer') or '')}",
        )

        response = self.post(
            f"/v1/chat/sessions/{session_id}/query",
            {**self._identity_payload(f"request-session-detail-{self.run_id}"), "include_messages": True},
        )
        body = json_dict(response)
        messages = body.get("messages") or []
        self.record(
            "#10 会话详情（含历史）",
            response,
            status=200,
            ok=body.get("session_id") == session_id
            and [message.get("role") for message in messages][-2:] == ["user", "assistant"],
            note=f"messages={len(messages)} visible={[m.get('content_visible') for m in messages]}",
        )

        response = self.request(
            "PATCH",
            f"/v1/chat/sessions/{session_id}",
            {**self._identity_payload(f"request-session-rename-{self.run_id}"), "title": "冒烟会话（已改名）"},
        )
        self.record(
            "#11 修改会话标题",
            response,
            status=200,
            ok=json_dict(response).get("title") == "冒烟会话（已改名）",
        )

        response = self.request(
            "DELETE",
            f"/v1/chat/sessions/{session_id}/messages",
            self._identity_payload(f"request-session-clear-{self.run_id}"),
        )
        body = json_dict(response)
        self.record(
            "#13 清空会话历史",
            response,
            status=200,
            ok=body.get("cleared") is True and body.get("session_id") == session_id,
        )

        response = self.post(
            f"/v1/chat/requests/{request_id}/query",
            self._identity_payload(f"request-result-cleared-{self.run_id}"),
        )
        body = json_dict(response)
        self.record(
            "#15 清空后查询生成结果",
            response,
            status=200,
            ok=body.get("status") == "completed"
            and body.get("result_visible") is False
            and body.get("reason") == REASON_SOURCE_UNAVAILABLE,
            note="结果本身仍是 completed，但答案已随清空下架：可见性按查询时刻重新判定",
        )

        response = self.request(
            "DELETE",
            f"/v1/chat/sessions/{session_id}",
            self._identity_payload(f"request-session-delete-{self.run_id}"),
        )
        self.record("#12 删除会话", response, status=200, ok=json_dict(response).get("deleted") is True)

        response = self.post(
            f"/v1/chat/sessions/{session_id}/query",
            self._identity_payload(f"request-session-gone-{self.run_id}"),
        )
        self.record(
            "#12 删除后再查会话",
            response,
            status=404,
            ok=error_code(json_dict(response)) == NOT_FOUND,
            note="已删除与「不存在 / 不属于本人」同口径",
        )


def body_ok(body: dict[str, Any], job_id: str) -> bool:
    """#01 受理回执的形状断言（202 的 status 枚举只有 queued/running/succeeded）。"""
    return body.get("job_id") == job_id and body.get("status") in {"queued", "running", "succeeded"}


def render_record(smoke: Smoke) -> str:
    """生成冒烟记录 Markdown（原 T0.1–T0.3 的证据表，POC 下线后仍用于联调记录）。"""
    passed = sum(1 for row in smoke.rows if row["passed"])
    skipped = sum(1 for row in smoke.rows if row.get("skeleton"))
    failed = [row for row in smoke.rows if not row["passed"]]
    lines = [
        f"# /v1 契约冒烟记录（{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}）",
        "",
        f"- 服务地址：`{smoke.base_url}`",
        f"- client_id：`{smoke.client_id}`",
        f"- 用例：{len(smoke.rows)} 条，通过 {passed} 条（其中未实现 {skipped} 条），失败 {len(failed)} 条",
        "",
        "## 一、用例明细",
        "",
        "| # | 用例 | 期望 HTTP | 实际 HTTP | 结论 | 备注 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for index, row in enumerate(smoke.rows, start=1):
        flag = "未实现" if row.get("skeleton") else ("通过" if row["passed"] else "**失败**")
        lines.append(f"| {index} | {row['name']} | {row['expected']} | {row['actual']} | {flag} | {row['note']} |")

    if failed:
        lines.extend(["", "## 二、失败明细", ""])
        lines.extend(
            f"- {row['name']}：期望 HTTP {row['expected']}，实际 {row['actual']}；{row['note']}" for row in failed
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Mock GSI：`/v1` 契约冒烟驱动器")
    parser.add_argument("--base-url", default="http://127.0.0.1:8002", help="AI 服务地址")
    parser.add_argument("--client-id", default=settings.CONTRACT_V1_CLIENT_ID, help="签名客户端标识")
    parser.add_argument("--secret", default=settings.CONTRACT_V1_SHARED_SECRET, help="共享密钥（与正式契约同一组配置）")
    parser.add_argument(
        "--user-id",
        default="1",
        help="模拟 GSI 登录用户的数字主键（字符串）；非数字会「无权限」，检索与问答均为 0 命中",
    )
    parser.add_argument("--no-chat", action="store_true", help="跳过 #08–#15 会话与问答（不调用外部模型，省费用）")
    parser.add_argument("--record", default="", help="冒烟记录输出路径（Markdown），留空只打印")
    parser.add_argument(
        "--allow-skeleton",
        action="store_true",
        help="把 501 骨架占位记为「未实现」而不是失败（阶段 0 之后业务接口尚未落地时用）",
    )
    args = parser.parse_args()

    # 样例文件服务与用例输出都在 stdout，HTTP/ORM 的 DEBUG 洪水会把 [PASS]/[FAIL] 埋掉
    for name in ("httpx", "httpcore", "sqlalchemy"):
        logging.getLogger(name).setLevel(logging.WARNING)
    for name in list(logging.root.manager.loggerDict):
        if isinstance(name, str) and name.startswith(("httpx", "httpcore", "sqlalchemy")):
            logging.getLogger(name).setLevel(logging.WARNING)

    smoke = Smoke(
        args.base_url,
        secret=args.secret,
        client_id=args.client_id,
        user_id=args.user_id,
        with_chat=not args.no_chat,
        allow_skeleton=args.allow_skeleton,
    )
    with FileServer() as files:
        print(f"[INFO] 样例文件地址（给 #01 的 file_url）：{files.url}")
        smoke.run(files.url)
    smoke.close()

    record = render_record(smoke)
    if args.record:
        target = Path(args.record)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(record, encoding="utf-8")
        print(f"[INFO] 冒烟记录已写入：{target}")
    else:
        print("\n" + record)

    failed = [row for row in smoke.rows if not row["passed"]]
    print(f"[INFO] 用例 {len(smoke.rows)} 条，失败 {len(failed)} 条")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
