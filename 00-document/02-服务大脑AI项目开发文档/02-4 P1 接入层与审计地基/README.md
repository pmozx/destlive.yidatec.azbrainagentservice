# 02-4 · P1 接入层与审计地基（T1.0–T1.5）

> P1 阶段的交付与验收产物。范围：23 张 `sbp_ai_` 表建模与 Alembic 基线、四头签名生产化（nonce 落库）、
> 三类幂等台账、契约错误码与错误体、`/v1` 审计扩展、20 条路由骨架。
> 执行日期 2026-10-29（计划原定 11-12~12-04）；执行人孟星旭。
> **M1 门禁结论：通过** —— 38 条验收项全绿（20 条路由 + 8 条签名/授权 + 5 条审计 + 5 条幂等台账），证据见 `01-M1验收记录.md`。

---

## 一、结论速览

| 维度 | 结论 |
| --- | --- |
| 四头签名 | **可用**：缺头 / 签名不符 / 时间戳越窗（±5 分钟）三类均 401 `UNAUTHORIZED`，401 带 `WWW-Authenticate` |
| 防重放 | **可用**：nonce 原子登记进 `sbp_ai_system_nonce`（跨实例共享、TTL 600s），同一组头第二次请求 401，审计记 `nonce_state=reused` |
| 授权块时效 | **可用**：`authorization` 跨度 >120s → 400 `INVALID_REQUEST`；已过期 → 401 `UNAUTHORIZED`（POC 疑点 Q1/Q2 的落地口径） |
| 幂等 | **可用**：`job` / `request` / `event` 三类台账；同 ID 同正文回放首次回执、异正文 409 `IDEMPOTENCY_CONFLICT`、删除类墓碑 `expires_at=NULL` 永不清理、失败可释放重试 |
| 错误码 | **可用**：15 个码覆盖 400/401/403/404/409/413/429/500/503，状态码与 `retryable` 由码推导，`error{code,message,retryable}` 三段式固定 |
| 审计扩展 | **生效**：本轮 27 条请求 27 条落库（含 7 条被拒），`security_check` 六字段 27/27 齐全，签名只存「前 8 + 后 4」摘要 |
| 20 条路由 | **可打通**：路径/方法与手册 §五 逐条一致；带签名拿到确定响应（骨架期统一 501 + `INTERNAL_ERROR`） |
| 门禁判断 | **通过**（计划 12-04，实际 10-29）；T2.x 起在骨架上填业务，接入层不再返工 |

---

## 二、文件清单

| 文件 | 用途 |
| --- | --- |
| `01-M1验收记录.md` | **M1 证据**：由 `python -m app.contract.verify_m1 --record` 生成，38 条验收项的期望/实际/结论 |
| `README.md` | 本文：产物说明、复跑方式、口径差异、遗留项 |

---

## 三、代码与迁移（`backend/`）

### 3.1 接入层：`app/contract/`

正式 `/v1` 与 `/api/v1`（管理台 JWT）分离；POC 验证骨架已按 `../02-3 POC 验证/03-POC下线与契约迁移方案.md` 阶段 0 下线（2026-10-30），`/v1` 只有本模块一份实现。`CONTRACT_V1_ENABLE=false` 表示 `/v1` 完全不挂载（404），不再提供 POC 回退。

| 文件 | 职责 | 任务 |
| --- | --- | --- |
| `__init__.py` | 唯一入口 `register_contract(app)` / `ensure_contract_store()`；密钥缺失只告警不阻断启动 | T1.5 |
| `errors.py` | 错误码全量表 + `ContractApiError` + `ContractApiRoute`（**继承审计路由类**：内层落审计、外层换错误体） | T1.2 |
| `signature.py` | **验签适配层**：算法在 `app/core/security_signature.py`（唯一实现），本模块只把失败翻成大写的契约码（`UNAUTHORIZED` / `SCOPE_REQUIRED` / `AUTHORIZATION_UNAVAILABLE`），并保留 `authorization` 形态检查 | T1.1 |
| `nonce.py` | nonce 原子登记：单条 `INSERT ... ON CONFLICT DO UPDATE ... WHERE expires_at < now`，无返回行即重放（与公共层 `NonceLedger` 同一语义，只是异步 + 落库） | T1.1 |
| `idempotency.py` | 三类台账预约/完成/释放/墓碑 + 过期清理；正文摘要**键序无关** | T1.3 |
| `audit.py` | `security_check` 六字段结构、时钟偏差计算（复用公共层 `timestamp_deviation_ms`）、`mark_failure`（签名通过后才失败的请求补记失败码） | T1.4 |
| `deps.py` | `verified_signature`（五头声明进 OpenAPI，声明为**可选**）、`idempotent_job/request/event`、`replay_response` | T1.1 / T1.3 |
| `router.py` | 20 条骨架路由 + 统一错误响应声明（400/401/403/404/409/413/422/429/501/503） | T1.5 |
| `verify_m1.py` | **M1 验收脚本**：Mock GSI 自签密钥逐条打通 20 路由，并核对审计与台账 | 验证 |
| `mock_gsi.py` | **GSI 侧冒烟驱动器**（阶段 0 由 POC `mock_client.py` 迁来）：本地托管样例文档给 `#01` 真实 `file_url`、负向用例逐条打契约、`--allow-skeleton` 把 501 记为「未实现」、`--record` 输出记录 | 联调 |

### 3.1.1 公共安全层（唯一实现）

POC 与 contract 曾各持一份验签实现：7 行原文拼装、±5 分钟窗口、nonce 格式、`authorization` 跨度四段几乎逐字重复，`_SIGNATURE_RULE` 文案也是两份拷贝。口径只要改一处、漏一处，联调就会以“签名失败”表现。现已收口，POC 下线后只剩 contract 一个消费者：

| 位置 | 职责 | 依赖方向 |
| --- | --- | --- |
| `app/core/security_signature.py` | **唯一算法实现**：五头常量与 `SIGNATURE_HEADERS` 清单、7 行原文与 HMAC 计算、时间窗、nonce 格式与防重放**时序**（先验签再登记）、`authorization` 跨度判定、OpenAPI 签名说明文案（`signature_rule_doc`）；失败抛中性的 `SignatureCheckError` / `AuthorizationCheckError`（带 `reason` 枚举），**不认识任何错误体** | 被 contract 依赖，不反向依赖接入层 |
| `setting.py` 的 `CONTRACT_*` 一组（5 项） | **唯一配置口径**：调用方标识、HMAC 共享密钥、时钟窗口、nonce TTL、`authorization` 跨度；contract 与 `mock_gsi` 都直读这一组，不再有第二份自签密钥/窗口；不另设值对象层 | 读 `Settings` |
| `app/contract/signature.py` | 适配层：`SignatureCheckError.reason` → 大写契约码；nonce 落库仍由 `nonce.py` 在 `deps.py` 里编排（同一时序） | → 公共层 |

口径边界（刻意不合并）：

1. **错误码归适配层**：公共层只给 `reason` 枚举，契约层映射为大写码（`UNAUTHORIZED` / `SCOPE_REQUIRED` / `AUTHORIZATION_UNAVAILABLE`），验收要求逐字一致。
2. **nonce 存储只有生产一种**：`InMemoryNonceLedger` 仅供单测与本地工具，生产走 `nonce.py` 落 `sbp_ai_system_nonce`（跨实例 + TTL）；共享的只是“验签通过后才登记”的时序。
3. **失败关闭语义共享**：共享密钥留空时公共层直接拒绝，契约层映为 503 `AUTHORIZATION_UNAVAILABLE`（可重试）；不会再出现“用空密钥算 HMAC 也能通过”的情况。

验证：`tests/test_security_signature.py` 12 条（算法只在公共层、同一失败原因按契约码归口、nonce 时序、`authorization` 归口、配置只有一组）。全量：`pytest -q` → **56 条**。

### 3.2 数据模型与迁移：T1.0

- 23 张 `sbp_ai_` 表模型：`system` 6 / `kb` 8 / `voice` 8 / `integration` 1（过渡）；
- 基线迁移 `app/alembic/versions/20261029_5d5210a8fd5e_p1_baseline_contract_tables_and_audit_.py`，实库 `alembic upgrade head` 已执行、`alembic check` 无漂移；
- 审计扩展四列建在 `sbp_ai_system_api_log`：`auth_mode` / `client_id` / `request_id` / `security_check`(JSONB)；台账表 `sbp_ai_system_nonce`、`sbp_ai_system_idempotency`。

### 3.3 基座改动

| 位置 | 改动 |
| --- | --- |
| `app/core/router_class.py` | `set_audit_security()` 回填四字段；`ApiLogRecord` / `_build_log_data` 增四字段；`_should_record` 支持 `API_LOG_FORCE_PATHS`；`status` 口径改为 `>=400` 记失败；`RequestValidationError` 记 400 |
| `app/__init__.py` | lifespan 调 `ensure_contract_store()`（清理过期 nonce 与台账）；`create_app` 只调 `register_contract`（`register_poc` 已随 POC 下线删除） |
| `app/contract/mock_gsi.py` | **新增（阶段 0）**：POC 冒烟驱动器迁入契约侧，新增 `--allow-skeleton`；`--reset` 与 `poc_v1_metric` 耗时统计随临时表一并移除 |
| `app/core/security_signature.py` | **新增（2026-10-30 重构）**：验签 / nonce 防重放时序 / `authorization` 时效的唯一实现；contract 唯一消费者 |
| `app/config/setting.py` | `CONTRACT_*` 签名 5 项是**唯一一组**签名参数；`POC_*` 5 项已随下线删除；新增 `API_LOG_FORCE_PATHS` |
| `env/.env.*` | 四个环境文件均已加 CONTRACT 段：dev 填自签测试值 `dev-self-signed-secret`，test/uat/prod 的 `CONTRACT_V1_SHARED_SECRET` 留空待填（GSI 下发后由部署机填写）；`mock_gsi` 读取同一组值，无独立密钥项 |
| `tests/test_v1_contract.py` | 31 条单测：错误码映射、签名原文与七类拒绝、密钥留空的失败关闭（503）、`authorization` 规则、幂等摘要与台账（真实 PG）、OpenAPI 口径、审计组装与落库、20 路由登记、HTTP 冒烟 |
| `tests/test_security_signature.py` | 12 条单测（2026-10-30 新增）：公共层为唯一实现、失败原因按契约码归口、nonce 时序、配置只有一组 |
| `tests/test_chat_cite.py` | 4 条单测（阶段 0 由 POC citation 用例迁来）：cites 顺序、snippet 关键词窗口、越界编号摘除 |

配置项（`backend/env/.env.*`）：前 5 项是**接口安全请求头**，contract 与 `mock_gsi` 共用同一组：

| 项 | 默认 | 说明 |
| --- | --- | --- |
| `CONTRACT_V1_ENABLE` | `true` | 关闭时 `/v1` 完全不挂载（404） |
| `CONTRACT_V1_CLIENT_ID` | `gsi-service` | 期望的 `X-AI-Signature-Client-Id` |
| `CONTRACT_V1_SHARED_SECRET` | 空 | **留空时 `/v1` 一律 503 `AUTHORIZATION_UNAVAILABLE`**（不能放行，否则等于关掉鉴权） |
| `CONTRACT_CLOCK_SKEW_MS` | `300000` | 时间戳窗口 ±5 分钟 |
| `CONTRACT_NONCE_TTL_SECONDS` | `600` | nonce 台账保留时长（覆盖窗口即可） |
| `CONTRACT_AUTHORIZATION_MAX_TTL_MS` | `120000` | `authorization` 最大跨度 |
| `CONTRACT_IDEMPOTENCY_TTL_DAYS` | `90` | 幂等台账 TTL（墓碑不受限） |
| `CONTRACT_TENANT_ID` | `gsi-default` | 契约 `tenant_id` 兜底 |

> **待填项**：仅 `CONTRACT_V1_SHARED_SECRET` 一项需要人工填（与 GSI 约定的 HMAC 共享密钥，连同 `CONTRACT_V1_CLIENT_ID` 属 GSI 下发物，计划 W10 到位）。口径：只写部署机上的 `backend/env/.env.<env>`，不入库、不进代码、不提交实值；填完重启进程生效。留空期间 `/v1` 全部 503 `AUTHORIZATION_UNAVAILABLE`（失败关闭），审计照常留痕。验证填对了没有：`python -m app.contract.verify_m1 --base-url http://<host>:<port> --secret <填的值>`。

### 3.4 运维视图：接入调用追踪

四张台账落地后，联调期排查只能翻库。补一套只读视图：**系统管理 → 接入调用追踪**（`/system/trace`），把四张表按身份拆成四个标签页，再按 `request_id` 串成一条链路。

| 层 | 文件 | 说明 |
| --- | --- | --- |
| 后端 | `app/modules/system/access_trace/{schema,service,controller}.py` | `/api/v1/system/access-trace` 下 7 个只读接口：summary / calls / calls/detail/{id} / nonces / idempotency / request-logs / timeline；只读聚合模块沿用 `integration_kb/stats` 先例，不建 `crud.py` |
| 后端 | `app/api/v1/routers.py` | `/system` 域登记 `AccessTraceRouter`（tags `102-接入调用追踪`） |
| 前端 | `src/api/module_system/accessTrace.js`、`src/views/module_system/trace/index.vue` | 6 个概览指标 + 四台账 + 调用详情 + 追踪弹窗；菜单由 `router/routes.js` 静态推导，不依赖后端菜单表 |
| 测试 | `tests/test_access_trace.py` | 9 条：7 路由登记、JSONB 子字段过滤、默认只看 `/v1`、四表串联、陌生 ID 不报错、概览口径、详情未命中、HTTP 冒烟 |

表 → 标签页的对应与各自能回答的问题：

| 标签页 | 表 | 回答什么 |
| --- | --- | --- |
| 调用明细 | `sbp_ai_system_api_log` | 谁在何时调了什么、响应码、签名/nonce 状态、被拒原因（`security_check`，含被拒请求） |
| 防重放台账 | `sbp_ai_system_nonce` | nonce 首次登记时间与过期点；只登记首用，重放不新增行 |
| 幂等台账 | `sbp_ai_system_idempotency` | 写请求/解析任务处于处理中、已回放还是墓碑（墓碑 `expires_at IS NULL`） |
| 结果台账 | `sbp_ai_system_request_log` | 超时后用 `request_id` 捞结果的承接方（过期给 `RESULT_EXPIRED` 而非 404） |

> **为什么幂等台账必须纳入**（需求最初只点了三张表）：409 `IDEMPOTENCY_CONFLICT` 在审计里只有一行错误码，分不清「重复提交被回放」「同 ID 异正文」「预约未释放」，只有 `idempotency.status` + `body_hash` 能区分；墓碑行是删除类接口的永久痕迹，审计表看不到。另外 `biz_id` 是业务键，与 `request_id` 不一定同源，因此 `timeline` 用「精确匹配 + `request_id-` 前缀」两段才不漏。

**只读设计**：不提供删除 / 清空。删 `nonce` 等于放开重放窗口，删幂等行会让重试从「回放」变成「重复执行」；过期清理由 lifespan 的 `ensure_contract_store()` 按 TTL 处理。

**概览口径**：`hours` 取 1–168（默认 24），只统计 `request_path LIKE '/v1/%'`；调用明细分页同样默认只看 `/v1`（不传 `request_path` 时生效，显式传路径才回溯 `/api/v1` 的管理台记录，面板与概览不会互相矛盾）；失败按 `status=1`（即 `response_code >= 400`）；签名失败 / 重放拦截 / 幂等冲突取 `security_check` 内字段；「在途请求」= `request_log.status='running'`。复跑：`.venv\Scripts\python.exe -m pytest tests/test_access_trace.py -q`。

> **测试基建改动**：新增 `tests/conftest.py` 模块级清池 fixture，并在 `pytest.ini` 设 `asyncio_default_fixture_loop_scope = module`。原因：`asyncio_default_test_loop_scope = module` 下每个模块一条事件循环，而 `async_engine` 是进程级单例，上一模块循环关闭后池里的 asyncpg 连接被下一模块复用会抛 `Event loop is closed`，写库异常被 `_write_api_log_async` 吞掉后表现为下一模块的 `NoResultFound`。加模块级 `dispose()` 后全量 60 条稳定通过。

---

## 四、如何复跑

```bat
cd /d d:\workspace\ws_work\fastapi_admin_xian\service_brain_agent\backend

rem 1) 启动服务（/v1 契约路由随应用挂载）
.venv\Scripts\python.exe -m uvicorn app:create_app --factory --host 127.0.0.1 --port 8002

rem 2) 另开终端跑 M1 验收（需服务 + 数据库；--record 覆盖证据文件）
.venv\Scripts\python.exe -m app.contract.verify_m1 --base-url http://127.0.0.1:8002 ^
    --record "..\00-document\02-服务大脑AI项目开发文档\02-4 P1 接入层与审计地基\01-M1验收记录.md"

rem 3) 单测与静态检查
.venv\Scripts\python.exe -m pytest -q                    rem 全量 56 条
.venv\Scripts\python.exe -m ruff check app tests
```

| 参数 | 说明 |
| --- | --- |
| `--skip-db` | 只验 HTTP（无库环境）；审计与台账 10 条不核对，**不能作为 M1 门禁** |
| `--batch <tag>` | 请求 ID 批次号（默认按时间生成），用于把本轮请求从历史审计里区分开 |
| `--secret` | 默认取 `CONTRACT_V1_SHARED_SECRET`；为空时脚本直接退出 1 |

退出码非 0 表示有验收项不达标；脚本可重复执行（幂等项用批次号隔离，不会撞历史台账）。

---

## 五、口径与偏差说明

1. **错误码取大写下划线**（`INVALID_REQUEST` / `UNAUTHORIZED` …），与手册 §七 表格、各接口错误示例逐字一致。POC 期用小写（`invalid_request`）是验证期自定口径，**不沿用**；POC 已下线（2026-10-30），对外只有大写一套。
2. **骨架期统一 501 + `INTERNAL_ERROR`**（`retryable=false`）。契约没有「未实现」专用码，选 `INTERNAL_ERROR` 是因为它本身就是 5xx 兜底码；GSI 联调时按「501 = 接口已接入但业务未交付」识别，不会误判成 4xx 语义错误。
3. **`sbp_ai_system_api_log.status` 口径变更**：由「`>=500` 记失败」改为「`>=400` 记失败」。4xx 在本期是**被拒请求**（鉴权、幂等、校验），按成功记账会让审计看板的失败率漏统计；`/api/v1` 的历史统计口径随之变化，改版前数据不做回填。
4. **`/v1` 的 GET 也留痕**：新增 `API_LOG_FORCE_PATHS=["/v1"]`（#02 轮询是 GET，按方法白名单会被过滤掉），保证 20 个接口全量落 `sbp_ai_system_api_log`。
5. **`security_check.failure_code` 在出口补记**：验签依赖只能记签名阶段的失败；`authorization` 超时效、幂等异正文、参数校验这些「签名已通过才失败」的请求，由 `ContractApiRoute` 出口统一补 `failure_code`，否则审计里只有 4xx 没有原因。
6. **幂等回放的 HTTP 演示要等业务落地**：骨架期 501 属失败，依赖退出会 `release` 预约（否则 ID 被永久锁在 `processing`），所以 HTTP 层无法演示「同 ID 回放」。M1 用**台账直连校验**（`reserve → complete → replay → 冲突 → 墓碑 → 释放`）钉住口径，M2 起在 #01 真实受理链路上补 HTTP 级回放用例。
7. **POC 测试已随下线处置（2026-10-30）**：`tests/test_poc_docs.py` 删除，其断言（签名头可选、7 行说明、错误体模型）已由 `tests/test_v1_contract.py` 完整覆盖；`tests/test_poc_signature.py` 删除，citation 用例迁为 `tests/test_chat_cite.py`，验签用例由 `tests/test_v1_contract.py` + `tests/test_security_signature.py` 覆盖。
8. **签名实现已收口为一份（2026-10-30）**：不要再往 `app/contract/signature.py` 里加算法（原文拼装、时间窗、nonce 格式、`authorization` 跨度），改口径只改 `app/core/security_signature.py`；适配层只允许改“失败原因 → 错误码”的映射。签名参数也只有一组（`CONTRACT_*` 5 项），新增接入方复用同一组配置，而不是复制一套验签代码或另起一份密钥。

---

## 六、遗留与下一阶段

| 项 | 现状 | 下一步 |
| --- | --- | --- |
| 20 条接口业务 | 501 骨架 | T2.1 起按 Tx 逐个替换 `_skeleton()`，签名/审计/幂等依赖不再改 |
| 限流 429 | 只有错误码与声明 | T7.5 性能与限流阶段落地（`Retry-After` 已在错误体出口预留） |
| 请求级超时查询 #15 | 只有骨架 | 与 #14 一并做 `request_log`，覆盖 completion / mutation / delete 三类 |
| POC 代码 | **已下线（2026-10-30）**：`app/poc/` 删除、启动不再建 `poc_v1_*`、6 张临时表与 `POC_*` 配置已清理；验签算法保留在 `app/core/security_signature.py`，冒烟驱动器迁为 `app/contract/mock_gsi.py` | 5 条接口向 contract 的迁移落点与验收断言见 `../02-3 POC 验证/03-POC下线与契约迁移方案.md`；原实现见 tag `poc-m0-frozen` |
| 生产密钥 | dev 用自签密钥 | 部署时经环境变量注入，轮换流程写进 T8.2 上线检查单 |
