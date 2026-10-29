# 02-3 · POC 验证（T0.1–T0.4）

> P0 阶段的验证产物。范围：`#01 受理 → #02 轮询 → #03 写入 → #07 检索 → #14 生成` 最小链路 + 四头 HMAC 签名冒烟。
> 执行日期 2026-10-29（计划原定 11-02~11-11，实际提前完成）；执行人孟星旭。
> **M0 门禁结论：链路可行，可启动 T1.x；17 条冒烟用例全绿，遗留 14 条契约疑点见 `02`。**

---

## 一、结论速览

| 维度 | 结论 |
| --- | --- |
| 链路可行性 | **可行**：#01→#02→#03→#07→#14 全链路在 POC 骨架下跑通，17/17 用例通过 |
| 四头签名 | **可行**：缺头/签名不符/时间戳越窗/nonce 重放四类均按契约返回 401 `unauthorized` |
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

---

## 三、代码位置（`backend/`）

POC 代码统一在 `app/poc/`，**定位为验证骨架，正式实现自 T1.x 起按生产标准重做，不直接沿用**。

| 文件 | 职责 |
| --- | --- |
| `__init__.py` | 唯一入口 `register_poc(app)` / `ensure_poc_store()`；`POC_V1_ENABLE=false` 时整体跳过 |
| `signature.py` | 四头 HMAC-SHA256 签名原文拼装与校验、±5 分钟窗口、进程内 nonce 登记、`authorization` 跨度校验 |
| `deps.py` | `verified_signature`（签名依赖；同时把五个签名头声明进 OpenAPI 供 Swagger 展示）、`poc_db`（显式事务边界，失败整体回滚） |
| `errors.py` | 错误码常量 + `PocApiRoute`（裸 `{"error":{...}}` 错误体，不动基座全局异常处理器）+ `PocErrorResponse`（错误体的 OpenAPI 模型） |
| `schemas.py` | 5 个请求模型 + `request_digest`（幂等正文指纹） |
| `router.py` | 5 个端点，只做「校验顺序 + 事务边界 + 回执拼装」 |
| `parser.py` | `#01/#02`：拉文件 → 解析 → 分片 → 落 `poc_v1_parse_job` |
| `publish.py` | `#03`：切片集合校验 → bge-m3 向量化 → `poc_v1_chunk` upsert（pgvector + HNSW） |
| `retrieval.py` | `#07/#14` 检索层：关键词 / 向量 / RRF 融合 / 结果投影 |
| `chat.py` | `#14`：检索 → 拼上下文 → LLM 生成 → citations（逐字引文）/ source_snapshots |
| `store.py` | `poc_v1_*` 建表、幂等台账、动作与阶段耗时落库 |
| `models.py` | 6 张临时表模型（`poc_v1_parse_job/publication/chunk/idempotency/chat_message/metric`） |
| `metrics.py` | `StageTimer`：按阶段计时，落 `poc_v1_metric.stages` |
| `mock_client.py` | **Mock GSI 冒烟驱动器**：本地起 HTTP 服务托管样例文档给 `#01` 真实 `file_url`，按契约逐条打用例，可 `--record` 输出 Markdown |

配套改动：`app/config/setting.py` 新增 `POC_*` 配置块（9 项）；`app/__init__.py` 在 lifespan 建表、在 `create_app` 挂路由；`tests/test_poc_signature.py` 17 条单测（不依赖服务与数据库）、`tests/test_poc_docs.py` 3 条 OpenAPI 口径断言（签名头必须在 `/docs` 上可见）。

**接口文档口径**：`/docs` 上每个 `/v1` 操作都会列出五个签名头（`X-AI-Signature-Version` / `-Client-Id` / `-Timestamp` / `-Nonce` / `-Signature`），`X-AI-Signature` 的说明里写全了 7 行签名原文与 LF 规则；响应侧固定展示 400 / 401（+ 端点相关的 404 / 409 / 413 / 500）的契约错误体；FastAPI 默认追加的 422 被改写成「POC 不返回 422，校验失败归一为 400」。

> 签名头在 OpenAPI 里**声明为可选**，这是刻意为之：声明成必填会让 FastAPI 先抛参数校验错误，把契约要求的 401 `unauthorized` 变成 400 `invalid_request`。真实校验仍在 `verify_signed_request` 里做，交互式调试请按 `mock_client.py` 的方式现算签名头。

---

## 四、如何复跑

前提：`backend/env/.env.dev` 指向可用的 Postgres（含 pgvector）与模型服务；`POC_V1_ENABLE=true`（默认）。

```bat
cd /d d:\workspace\ws_work\fastapi_admin_xian\service_brain_agent\backend

rem 1) 启动服务（POC 路由随应用一起挂载）
.venv\Scripts\python.exe -m uvicorn app:create_app --factory --host 127.0.0.1 --port 8003

rem 2) 另开一个终端跑冒烟（--reset 先清 poc_v1_*，跑基线必加）
.venv\Scripts\python.exe -m app.poc.mock_client --base-url http://127.0.0.1:8003 --reset ^
    --record "..\00-document\02-服务大脑AI项目开发文档\02-3 POC 验证\01-POC冒烟记录.md"

rem 3) 单测（不需要服务）
.venv\Scripts\python.exe -m pytest tests\test_poc_signature.py -q
```

| 参数 | 说明 |
| --- | --- |
| `--no-chat` | 跳过 `#14`（不调用外部模型，改契约时先跑这个省费用） |
| `--reset` | 清空 6 张 `poc_v1_*` 临时表；不加则历史发布会累积，`#07` 命中数逐轮增长 |
| `--record <path>` | 输出 Markdown 冒烟记录（含阶段耗时汇总表） |

退出码非 0 表示有用例不符合预期，可直接作为 T1.x 的回归脚本。
