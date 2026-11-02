# 02 · 日志审计与接入追踪

> **一句话定位**：把每一次接口调用（含被拒的）留成可检索的审计行，并让运维能用一个 `request_id` 把「调用 → 防重放 → 幂等 → 结果」四段证据串起来。
> 归属域 `system`（子模块 `api_log` 负责写与查，`access_trace` 负责跨表只读视图）· 数据表 4 张 · 接口 11 个 · 前端页面 2 个 · 版本 v1.0（2026-11-02）

---

## 一、边界

**这个模块做**

- 采集 `/api/v1`（管理台 JWT）与 `/v1`（GSI 契约 HMAC）两类请求的审计行：路径、方法、参数、响应码、耗时、来源 IP、操作人、鉴权上下文。
- 采集**被拒绝的请求**（4xx/5xx、参数校验失败、验签失败、nonce 重放），并把拒绝原因结构化落 `security_check`。
- 提供审计行的分页查询、详情、批量删除、按保留天数清理。
- 提供契约调用的只读取证视图：概览计数、调用明细、防重放台账、幂等台账、结果台账、按 `request_id` 的四表时间线。
- 维护三张技术台账的写入与 TTL 清理：nonce（防重放）、idempotency（幂等）、request_log（结果，**仅建表与只读，暂无写入方**）。

**这个模块不做**

- 不产生业务动作：删除审计行不会影响任何业务数据；清空 nonce/幂等台账的入口**故意不提供**（删掉 nonce 会让重放立刻通过，删掉幂等键会让同一请求重复执行）。
- 不做登录日志/操作日志之外的行为分析：没有报表、没有告警规则、没有留存策略分级（只有全局保留天数）。
- 不做日志采集的旁路（agent/sidecar）：采集在应用进程内，靠 `APIRoute` 子类实现，进程外无感知。
- 不记录响应体超过阈值的内容，不记录文件流内容，不记录 GET 查询（`/v1` 除外）。

---

## 二、能力地图

按使用者视角分两组，代码上分别对应 `api_log` 与 `access_trace`：

| 能力 | 使用者 | 入口 | 涉及表 |
| --- | --- | --- | --- |
| 自动采集每一次写请求（含被拒） | 系统（零侵入） | 路由类 `ApiLogRoute` | `api_log` |
| 按操作人/路径/方法/状态/IP 查审计行 | 运维、开发 | 页面「日志审计」→ 4 个端点 | `api_log` |
| 看单条审计的原始请求/响应报文 | 开发联调 | 详情端点 | `api_log` |
| 删错行、按天数清理历史 | 运维 | 删除 / 清理端点 | `api_log` |
| 看近 N 小时契约调用质量（失败/签名失败/重放/409/在途） | 运维、对接方 | 页面「接入调用追踪」顶栏 → 概览端点 | `api_log` + 三张台账 |
| 按签名结果 / nonce 状态筛契约调用 | 对接方排查 | 调用明细分页端点（JSONB 过滤） | `api_log.security_check` |
| 查某个 nonce 是否首次使用、何时过期 | 对接方、安全 | 防重放台账端点 | `nonce` |
| 查某个业务键的幂等状态（回放/冲突/墓碑） | 对接方、开发 | 幂等台账端点 | `idempotency` |
| 查超时请求的最终结果 | 对接方 | 结果台账端点（**当前恒空**） | `request_log` |
| 用一个 `request_id` 串起四段证据 | 运维、开发 | 追踪端点 | 四表 |

---

## 三、表速查

| 表名 | 中文名 | 角色 | 写入方 | 保留策略 |
| --- | --- | --- | --- | --- |
| `sbp_ai_system_api_log` | 接口审计日志 | 明细（只追加） | `core/router_class.py::ApiLogRoute` → 进程内异步任务 | 90 天（启动清理 + 手动清理，物理删除） |
| `sbp_ai_system_nonce` | 防重放台账 | 台账（状态机） | `contract/deps.py::verified_signature` → `SystemNonceService.register` | 600 秒（启动清理，物理删除；过期行可被新 nonce 接管） |
| `sbp_ai_system_idempotency` | 幂等台账 | 台账（状态机） | `contract/deps.py::_idempotent_guard` → `SystemIdempotencyService` | 90 天（启动清理）；**墓碑永不清** |
| `sbp_ai_system_request_log` | 结果台账 | 台账（设计态） | **无（未接入）** | 设计 24 小时，**无清理任务** |

字段、键、索引与表间引用见 [`02-数据模型与表关系.md`](02-数据模型与表关系.md)；保留策略的完整口径见 [`01-业务功能说明.md`](01-业务功能说明.md) §六。

---

## 四、接口速查

前缀 `/api/v1/system`，全部为管理台 JWT 侧接口；写操作本身也会被审计（递归留痕一次，不会无限递归）。

| # | 方法 | 路径 | 用途 | 文档 |
| --- | --- | --- | --- | --- |
| F1 | GET | `/api-log/list` | 分页查审计行（不返回请求/响应大字段） | [03](03-接口清单.md) §3.1 |
| F2 | GET | `/api-log/detail/{id}` | 单条审计详情（含请求/响应体） | §3.2 |
| F3 | DELETE | `/api-log/delete` | 批量物理删除 | §3.3 |
| F4 | POST | `/api-log/clean` | 按保留天数清理 | §3.4 |
| F5 | GET | `/access-trace/summary` | 近 N 小时概览（9 个计数） | §3.5 |
| F6 | GET | `/access-trace/calls` | 契约调用明细分页（JSONB 过滤） | §3.6 |
| F7 | GET | `/access-trace/calls/detail/{id}` | 调用详情（含报文） | §3.7 |
| F8 | GET | `/access-trace/nonces` | 防重放台账分页 | §3.8 |
| F9 | GET | `/access-trace/idempotency` | 幂等台账分页 | §3.9 |
| F10 | GET | `/access-trace/request-logs` | 结果台账分页 | §3.10 |
| F11 | GET | `/access-trace/timeline` | 按 `request_id` 串联四表 | §3.11 |

---

## 五、代码地图

| 路径 | 职责 | 备注 |
| --- | --- | --- |
| `app/core/router_class.py` | 采集与异步落库：`ApiLogRoute`、`set_audit_username`、`set_audit_security`、`_redact_sensitive`、`flush_pending_api_logs` | **本模块的写入核心**，被 `/v1` 的 `ContractApiRoute` 继承 |
| `app/modules/system/api_log/` | 表模型、写入/查询 schema、CRUD、查询与清理服务、4 个端点 | `crud.py` 里写明了"审计表走物理删除"的原因 |
| `app/modules/system/access_trace/` | 四表只读视图：service（时间线/概览/分页）、crud（复用四个 CRUD 的薄桥接）、schema、7 个端点 | `model.py` 只做集中导出，避免模型复制 |
| `app/modules/system/nonce/` | 防重放台账：原子登记（`ON CONFLICT ... WHERE expires_at < now`）+ 过期清理 | `crud.py` 的单条 SQL 是"首用唯一事实来源" |
| `app/modules/system/idempotency/` | 幂等台账：预约、接管过期、登记结果、释放、墓碑、清理 | 返回中性状态，不依赖 `app.contract` |
| `app/modules/system/request_log/` | 结果台账模型 + 只读 CRUD | 无写入方、无清理任务 |
| `app/contract/deps.py` | `verified_signature`（逐步回填 `security_check`）、`_idempotent_guard`（预约/释放/登记）、`contract_store_ready`（启动清理） | 台账的**业务写入时机**由这里决定 |
| `app/contract/audit.py` | `SecurityRecord`：`security_check` 六字段结构与取值常量 | 定义"留痕长什么样" |
| `app/contract/idempotency.py`、`app/contract/nonce.py` | 契约层适配：把台账中性状态翻译成 409/503 契约错误码 | 反向依赖方向：`contract → modules/system` |
| `app/modules/system/bootstrap.py` | 建表清单（审计表受 `API_LOG_AUTO_CREATE` 控制） | 表由启动期建出，项目不使用迁移 |
| `app/__init__.py`（lifespan） | 启动：`purge_expired()` 清审计 → `ensure_contract_store()` 清 nonce/幂等；关停：`flush_pending_api_logs()` | 失败只告警不阻断 |
| `frontend/src/views/module_system/log/`、`trace/` | 两个页面；接口封装在 `frontend/src/api/module_system/{log,accessTrace}.js` | 页面不做本地过滤，全下推后端 |

---

## 六、术语

| 术语 | 含义 |
| --- | --- |
| 审计行 | `sbp_ai_system_api_log` 的一条记录，一次 HTTP 请求对应一行（含被拒） |
| 四头 | 契约 `/v1` 必带的 `X-AI-Signature-Version` / `-Client-Id` / `-Timestamp` / `-Nonce` + `X-AI-Signature`，共 5 个头部 |
| `security_check` | 审计行里的 JSONB，记录四头校验过程：到达了哪些头、时钟偏差、nonce 状态、签名结果、签名摘要、失败码 |
| `first_use` / `reused` / `not_checked` | nonce 三态：本次首用已登记 / 窗口内重复被拒 / 未走到 nonce 校验（签名就已失败） |
| `passed` / `failed` / `skipped` | 签名三态：通过 / 不通过 / 未校验 |
| 回放（replay） | 同一幂等键 + 完全相同正文的重试，直接返回首次结果 |
| `processing` / `succeeded` / `failed` / `tombstone` | 幂等台账四态；`tombstone` 用于删除类，`expires_at` 为空即永不清理 |
| `biz_id` | 幂等业务键，按域取 `job_id` / `request_id` / `event_id`，**不等于** `api_log.request_id` |
| `scope` | 幂等域：`job`（异步任务）/ `request`（同步写）/ `event`（变更事件） |
| 断链 | 按 `request_id` 追踪时某一段为空。多数属正常（请求没走到那一步），需按关系定义的"断链后果"判断 |

---

## 七、文档导航

| 文档 | 回答 |
| --- | --- |
| [`01-业务功能说明.md`](01-业务功能说明.md) | 谁在什么场景用什么功能；一次请求的生命周期；状态如何判定；数据留多久；现在做不到什么 |
| [`02-数据模型与表关系.md`](02-数据模型与表关系.md) | 四张表每列的含义；主键/唯一键/索引；表与表怎么对上（R1–R7）；谁写谁读；为什么没有物理外键 |
| [`03-接口清单.md`](03-接口清单.md) | 11 个端点怎么调；参数与返回；错误码；可复制示例；与前端函数对应关系 |
| [`04-实现与运维说明.md`](04-实现与运维说明.md) | 代码在哪；为什么这么实现；配置项与调错的后果；按症状排障；测试覆盖与已知问题 |
