"""契约测试用例数据访问：JSON 一律存放在 `00-document/06-.../test-data`，测试代码不存数据。

为什么数据要搬出代码包：
1. 同一份入参既要给脚本用、又要在测试文档里逐字展示，两处各写一遍迟早漂移；
2. 签名摘要按“发出的原始字节”计算，数据以文件形式保存才能保证脚本与 curl/Apifox 发的是同一份字节。

目录约定见 `test-data/README.md`：
   requests/{接口编号}_{场景}.json   人工维护的请求正文
   responses/                        脚本回写的实测结果
"""

import hashlib
import json
import time
from pathlib import Path
from typing import Any

# backend/tests/contract/case_data.py → 仓库根目录
_REPO_ROOT = Path(__file__).resolve().parents[3]
CASE_DATA_ROOT = _REPO_ROOT / "00-document" / "06-服务大脑AI项目测试用例与结果文档" / "test-data"
REQUEST_DIR = CASE_DATA_ROOT / "requests"
RESULT_DIR = CASE_DATA_ROOT / "responses"

# 知识接口的整包摘要由运行期按 chunks 算出，JSON 里只写这个占位符
CONTENT_HASH_PLACEHOLDER = "__COMPUTED_AT_RUNTIME__"

# #07 的 authorization 时效块不能用静态值：一落地就过期。JSON 里写占位符，
# 发送前换算成相对当前时刻的毫秒值（键 → 相对 now 的偏移）。
AUTHORIZATION_OFFSETS_MS = {
    "__AUTH_ISSUED_AT__": -5_000,  # 5 秒前签发
    "__AUTH_EXPIRES_120S__": 115_000,  # 跨度恰好 120 秒（上限口径）
    "__AUTH_EXPIRES_300S__": 295_000,  # 跨度 300 秒，用于跨度超限
    "__AUTH_EXPIRED_ISSUED__": -60_000,  # 已过期场景：签发在 60 秒前
    "__AUTH_EXPIRED_EXPIRES__": -30_000,  # 已过期场景：到期在 30 秒前
}


def request_bytes(name: str) -> bytes:
    """读取请求正文的原始字节：签名摘要必须与实际发出的字节完全一致。"""
    path = REQUEST_DIR / name
    if not path.is_file():
        raise FileNotFoundError(f"测试用例数据不存在：{path}")
    return path.read_bytes()


def load_request(name: str) -> dict[str, Any]:
    """读取请求正文并解析为 dict（用于需要改字段后再序列化的场景）。"""
    return json.loads(request_bytes(name).decode("utf-8"))


def save_result(name: str, payload: Any) -> Path:
    """把本次执行的实测结果回写到数据目录，供测试文档直接引用。"""
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULT_DIR / name
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def compute_content_hash(chunks: list[dict]) -> str:
    """知识接口整包摘要规则（契约手册 §六.03）：固定字段、按键排序、紧凑分隔符。"""
    hash_fields = ("seq", "heading", "content", "page_from", "page_to")
    rows = [{f: chunk.get(f) for f in hash_fields} for chunk in chunks]
    rows.sort(key=lambda r: r["seq"])
    serialized = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def resolve_content_hash(payload: dict) -> dict:
    """把 `__COMPUTED_AT_RUNTIME__` 换成按 chunks 实算的摘要（已写死的摘要原样保留）。"""
    if payload.get("content_hash") != CONTENT_HASH_PLACEHOLDER:
        return payload
    return {**payload, "content_hash": compute_content_hash(payload["chunks"])}


def resolve_authorization(payload: dict) -> dict:
    """把 `authorization` 里的时效占位符换成发送时刻的 Unix 毫秒值。

    同一个 payload 内的占位符按**同一个 now** 换算，保证 issued_at / expires_at 的
    跨度与用例设计一致（跨度超限、已过期两种场景靠不同占位符组合表达）。
    """
    block = payload.get("authorization")
    if not isinstance(block, dict):
        return payload
    now = int(time.time() * 1000)
    resolved = {
        key: now + AUTHORIZATION_OFFSETS_MS[value] if isinstance(value, str) and value in AUTHORIZATION_OFFSETS_MS else value
        for key, value in block.items()
    }
    return {**payload, "authorization": resolved}
