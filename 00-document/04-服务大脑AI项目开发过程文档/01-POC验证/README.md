# 01 · POC 验证（T0.1–T0.4，P0 阶段）

> P0 阶段的验证产物。范围：`#01 受理 → #02 轮询 → #03 写入 → #07 检索 → #14 生成` 最小链路 + 五头 HMAC 签名冒烟。
> 执行日期 2026-10-29（计划原定 11-02~11-11，实际提前完成）；执行人孟星旭。
> **M0 门禁结论：链路可行，可启动 T1.x；17 条冒烟用例全绿，遗留 14 条契约疑点见 `02`。**

---

## 一、结论速览

| 维度 | 结论 |
| --- | --- |
| 链路可行性 | **可行**：#01→#02→#03→#07→#14 全链路在 POC 骨架下跑通，17/17 用例通过 |
| 五头签名 | **可行**：缺头/签名不符/时间戳越窗/nonce 重放四类均按契约返回 401 `unauthorized` |
| 检索质量 | **可行**：`keyword` 与 `natural_language`（向量+关键词 RRF）在同一份 3 切片样例上都能召回 |
| 生成与引用 | **可行**：`citations[].quote` 均为切片正文的逐字子串，可直接用于 GSI 高亮定位 |
| 耗时 | `#14` 端到端 16.9s（llm 16.1s，**同 prompt 曾测得 34.5s**）；`#03` 1.49s；`#07` 0.49~0.74s；`#01` 0.69s |
| 主要风险 | `#14` 模型侧波动大，`LLM_TIMEOUT=60s` 余量不足；契约 14 处口径未定义需与 GSI 对齐 |
| 门禁判断 | **通过**：不阻塞 T1.x 启动；疑点在 T1.x 开发与联调期与 GSI 逐条对齐 |

---

## 二、文件清单

| 文件 | 用途 |
| --- | --- |
| `01-POC冒烟记录.md` | **证据**：17 条用例明细、阶段耗时初测、首轮 8 条失败的根因与修复 |
| `02-POC结论与契约疑点清单.md` | **M0 交付物**：逐项可行性结论、耗时初测与预算判断、POC→正式实现的差异清单、14 条契约疑点（Q1–Q14） |
| `03-POC下线与契约迁移方案.md` | **T2.x 输入**：下线判定（含 `poc_v1_*` 启动副作用实测）、阶段 0 下线与收缩动作表、5 条接口向 contract 迁移的逐条落点/复用资产/验收断言（#01/#02/#03/#07/#14/#15） |

---

## 三、代码位置与下线状态（`backend/`）

**POC 代码已于 2026-10-30 按 `03-POC下线与契约迁移方案.md` 阶段 0 下线**：`app/poc/` 整目录删除，
`app/__init__.py` 不再调用 `ensure_poc_store()` / `register_poc()`，`setting.py` 的 5 项 `POC_*` 配置删除，
6 张 `poc_v1_*` 临时表已 `DROP`（`pg_tables like 'poc_v1_%'` 实测为空）。
对外能力零变化：`/v1` 本来就只有 `app/modules_api/` 的 20 条 501 骨架（现落在 `modules_api/kb/`）。

> **注（2026-11-02）**：下表是本阶段的处置记录，文件名保持当时状态。测试已按被测对象重组为
> `core/` / `contract/` / `modules/` / `stages/` 四个包，新旧对照见 `backend/tests/README.md`。

| 位置 | 现状 |
| --- | --- |
| tag `poc-m0-frozen` | POC 原实现的冻结基线；需要对照时 `git show poc-m0-frozen:backend/app/poc/xxx.py`（`service.py` 等未跟踪文件不在 tag 内） |
| `app/scripts/contract/mock_gsi.py` | 由 `mock_client.py` 迁来：本地托管样例文档给 `#01` 真实 `file_url`、逐条打契约用例、`--record` 输出 Markdown；新增 `--allow-skeleton` 把 501 记为「未实现」 |
| `app/core/security_signature.py` | **保留**：验签 / nonce 防重放时序 / `authorization` 时效的唯一算法实现 |
| `setting.py` 的 `CONTRACT_*` 5 项 | **保留**：唯一一组签名参数（调用方标识 / 共享密钥 / 时钟窗口 / nonce TTL / 授权跨度），POC 专属配置已删 |
| `tests/test_chat_cite.py` | 由 POC citation 用例迁来，断言生产侧 `integration_chat.cite` 的 cites 顺序、snippet 窗口与越界编号摘除 |
| `tests/test_v1_contract.py` | 接管原 POC OpenAPI 口径断言（签名头可选、7 行说明、契约错误体） |

### 3.1 公共安全层（保留，不随 POC 下线）

POC 与正式接入层曾各持一份验签实现（原文拼装、时间窗、nonce 格式、`authorization` 跨度几乎逐字重复），
2026-10-30 已收口为单份实现；POC 下线时只删适配层，公共层原样留给 contract：

| 位置 | 职责 | 谁在用 |
| --- | --- | --- |
| `app/core/security_signature.py` | **唯一算法实现**：五头常量、7 行签名原文、HMAC 计算、时间窗、nonce 格式与防重放时序、`authorization` 跨度判定；失败抛中性的 `SignatureCheckError` / `AuthorizationCheckError`（按 `reason` 枚举区分原因），不认识任何错误体 | contract |
| `setting.py` 的 `CONTRACT_*` 5 项 | **唯一一套**签名参数：调用方标识、共享密钥、时钟窗口 300s、`authorization` 跨度 120s、nonce TTL 600s；密钥留空＝失败关闭，不另设值对象层 | contract |
| `app/modules_api/base/signature.py` | **适配层**：翻译成大写码（`UNAUTHORIZED` / `SCOPE_REQUIRED` / `AUTHORIZATION_UNAVAILABLE`），nonce 用落库台账 | contract |

依赖方向：`app/modules_api/base` → `app/core/security_signature.py`，公共层**不反向依赖**任何接入层。验证：`pytest tests/test_security_signature.py -q`（12 条，锁定“算法只在公共层、同一失败原因按契约错误码归口、nonce 先验签再登记”）。

**接口文档口径**：`/docs` 上每个 `/v1` 操作都会列出五个签名头（`X-AI-Signature-Version` / `-Client-Id` / `-Timestamp` / `-Nonce` / `-Signature`），`X-AI-Signature` 的说明里写全了 7 行签名原文与 LF 规则；响应侧固定展示 400 / 401（+ 端点相关的 403 / 404 / 409 / 413 / 429 / 501 / 503）的契约错误体；FastAPI 默认追加的 422 被改写成「校验失败归一为 400」。

> 签名头在 OpenAPI 里**声明为可选**，这是刻意为之：声明成必填会让 FastAPI 先抛参数校验错误，把契约要求的 401 `UNAUTHORIZED` 变成 400 `INVALID_REQUEST`。真实校验在 `app/modules_api/base/deps.py` 验签依赖里做（算法在 `app/core/security_signature.py`），交互式调试请按 `app/scripts/contract/mock_gsi.py` 的方式现算签名头。

---

## 四、如何复跑

前提：`backend/env/.env.dev` 指向可用的 Postgres（含 pgvector）与模型服务；
`CONTRACT_V1_SHARED_SECRET` 已填（留空时 `/v1` 一律 503 `AUTHORIZATION_UNAVAILABLE`）。

```bat
cd /d d:\workspace\ws_work\fastapi_admin_xian\service_brain_agent\backend

rem 1) 启动服务（/v1 只挂 contract 的 20 条骨架）
.venv\Scripts\python.exe -m uvicorn app:create_app --factory --host 127.0.0.1 --port 8002

rem 2) M1 门禁（签名 + 台账 + 审计，证据由 02-4 保管）
.venv\Scripts\python.exe -m app.scripts.contract.verify_m1 --base-url http://127.0.0.1:8002 ^
    --record "..\00-document\04-服务大脑AI项目开发过程文档\02-P1接入层与审计验收\01-M1验收记录.md"

rem 3) GSI 侧冒烟（骨架期加 --allow-skeleton，501 记为未实现；留档时再加 --record 另存新文件）
.venv\Scripts\python.exe -m app.scripts.contract.mock_gsi --base-url http://127.0.0.1:8002 --allow-skeleton

rem 4) 单测与静态检查（不需要服务）
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m ruff check app tests
```

| 参数 | 说明 |
| --- | --- |
| `--no-chat` | 跳过 `#14`（不调用外部模型，改契约时先跑这个省费用） |
| `--allow-skeleton` | 把 501 骨架占位记为「未实现」而不是失败；业务随 Tx 落地后去掉 |
| `--record <path>` | 输出 Markdown 冒烟记录（用例明细 + 失败明细）；`01-POC冒烟记录.md` 是 POC 期（真实业务）的历史证据，**不要覆盖**，重跑另存新文件 |

退出码非 0 表示有用例不符合预期，可直接作为 T1.x/T2.x 的回归脚本。

> 两个脚本分工（`03` §五 风险 7）：`verify_m1` 保「M1 门禁 + 台账核对证据」，`mock_gsi` 保「GSI 侧样例 + 负向用例 + 联调」。
