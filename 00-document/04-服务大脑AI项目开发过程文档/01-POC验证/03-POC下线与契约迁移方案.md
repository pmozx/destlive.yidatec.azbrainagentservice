# 03 · POC 下线与 `/v1` 接口迁移方案

> 定位：本文是 **T2.x 的输入**，不是执行记录。结论一句话——**POC 可以下线，但"接口功能完全迁移到 contract"不是搬代码，而是按 T2→T5 的 5 条链路按生产口径重做**；
> 阶段 0（下线 + 收缩）可立即执行且**不改变对外能力**（现状默认已是 501 骨架），阶段 1–5 随计划任务逐条落地。
> 编写日期 2026-10-29；对应计划见 `02-03` §四。
>
> **阶段 0 执行状态：已完成（2026-10-30）** —— `app/poc/` 已删除（基线 tag `poc-m0-frozen`）、启动不再建表、
> 6 张 `poc_v1_*` 已 `DROP`、`POC_*` 5 项配置已删、Mock 驱动器迁为 `app/contract/mock_gsi.py`（新增 `--allow-skeleton`）。
> 下文§一的现状判定是**执行前的快照**，保留作对照；阶段 1–5 仍未开始。

---

## 一、现状判定（证据）

| 维度 | 现状 | 证据 |
| --- | --- | --- |
| 路由归属 | `/v1` 已由 contract 接管：`CONTRACT_V1_ENABLE=true`（默认）时 `register_poc` 首行返回 | `app/poc/__init__.py:26`、`app/__init__.py:172-179` |
| 实现完整度 | contract 20 条**全部** `_skeleton()` → 501 + `INTERNAL_ERROR`；POC 只有 5 条（#01/#02/#03/#07/#14）是真实现 | `app/contract/router.py`（20 处 `_skeleton`）、`app/poc/router.py` |
| 启动副作用 | `ensure_poc_store()` **只判** `POC_V1_ENABLE`、不判 `CONTRACT_V1_ENABLE`，所以每次启动仍在库里建 6 张 `poc_v1_*` | `app/poc/__init__.py:42-48`、`app/__init__.py:63-70` |
| 残留数据（dev 实测） | `poc_v1_parse_job` 1 / `publication` 1 / `chunk` 3 / `idempotency` 3 / `chat_message` 2 / `metric` 6 | 2026-10-29 直查 information_schema |
| 数据与安全口径 | 落 `poc_v1_*` 临时表、nonce 在进程内存、无审计、幂等单表永久有效 | `02-POC结论与契约疑点清单.md` §四 |
| 公共安全层 | 已收口（2026-10-30）：验签/防重放/`authorization` 时效的**算法只有一份**（`app/core/security_signature.py`），签名参数也只有一组（`setting.py` 的 `CONTRACT_*` 5 项，POC 与 contract 共用）；POC 与 contract 均只保留错误码适配层 | `app/core/security_signature.py`、`app/config/setting.py` |
| 测试 | `test_poc_signature.py` 17 条（签名 4 类拒绝 + citation 逐字引文）；`test_poc_docs.py` 3 条（实际断言的是 **contract** 的 OpenAPI，仅从 poc 取常量）；`test_security_signature.py` 11 条（公共层两套口径等价） | `backend/tests/` |

**判定**：POC 在运行期已"实休"，只剩两件事没做干净——启动时仍建临时表、代码与测试仍在主干里占位。

---

## 二、阶段 0：下线与收缩（可立即执行，约 0.25 人日）

目标：`/v1` 只保留**一份**实现（contract），POC 不再有挂载路径、不再产生库副作用；**对外能力零变化**（本来 501）。

| # | 动作 | 落点 | 说明 | 验收 |
| --- | --- | --- | --- | --- |
| 0.1 | 冻结基线 | `git tag poc-m0-frozen` | 删除后需对照 POC 实现时 `git show poc-m0-frozen:backend/app/poc/xxx.py` | tag 存在，含 02-3 全部证据文件 |
| 0.2 | 断启动入口 | `app/__init__.py` | 删 `ensure_poc_store()`（63-70）与 `register_poc(app)`（176-179），同步 lifespan docstring 第 15 行 | 启动日志无 `POC /v1`；库中不再新建 `poc_v1_*` |
| 0.3 | 删实现代码 | `app/poc/` 12 个文件（`__init__ / router / deps / errors / schemas / signature / store / models / metrics / parser / publish / retrieval / chat`） | 约 98KB；`metadata`/`mock_client` 单独处理（见 0.4）。**注意保留公共层**：算法在 `app/core/security_signature.py`、签名参数在 `setting.py` 的 `CONTRACT_*` 组，删 POC 时都不要动（contract 仍依赖）；`app/poc/signature.py` 里只剩适配层与进程内 nonce 台账 | `grep -r "app.poc" backend/` 仅剩测试历史注释；`grep -r "security_signature" backend/app` 仍有 contract 引用 |
| 0.4 | 迁移 Mock 驱动器 | `app/poc/mock_client.py` → `app/contract/mock_gsi.py` | 改 2 处：`from .signature import build_signed_headers` → `app.contract.signature`；期望错误码 lower → UPPER。签名配置无需改（已直读共用的 `CONTRACT_*`）。新增 `--allow-skeleton`：遇 501 记为「未实现」而非失败 | `python -m app.contract.mock_gsi --base-url ... --allow-skeleton` 在骨架期退出码 0 |
| 0.5 | 迁移测试 | `tests/test_poc_docs.py` → `tests/test_contract_docs.py`（import 改 `app.contract.signature.SIGNATURE_HEADERS`）；`tests/test_poc_signature.py` 拆解 | 签名 4 类拒绝已由 `test_v1_contract.py` 覆盖 → 直接删；citation 3 条迁到 `tests/test_chat_cite.py`，指向 `integration_chat.cite.build_cites`；`test_security_signature.py` 全部指向公共层与 contract，**不受 POC 下线影响** | `pytest -q` 全绿，条数 = 71 − 17 + 3 ≈ 57 |
| 0.6 | 配置下线 | `app/config/setting.py` 删 `POC_V1_ENABLE` / `POC_V1_MAX_FILE_MB` / `POC_RETRIEVE_*` / `POC_RRF_K` 共 5 项（签名参数已并入 `CONTRACT_*`，不在删除范围）；`backend/env/.env.{dev,test,uat,prod}` 删对应行 | 同时改 `CONTRACT_V1_ENABLE` 描述：关闭后 `/v1` **完全不挂载**（404），不再是「恢复 POC」 | 四环境加载无 `POC_` 键；`grep -rn "POC_V1_" backend/` 无结果 |
| 0.7 | 清理临时表 | 手动 SQL（dev/prod 各一次） | 先备份再删：`CREATE TABLE poc_v1_backup_20261029 AS SELECT ...` 或直接确认「仅冒烟数据、无业务价值」后 `DROP TABLE poc_v1_parse_job, poc_v1_publication, poc_v1_chunk, poc_v1_idempotency, poc_v1_chat_message, poc_v1_metric` | `information_schema.tables like 'poc_v1_%'` 为空 |
| 0.8 | 文档收口 | 02-3 `README.md` §三 改为「代码已下线（tag `poc-m0-frozen`）」，§四 复跑命令改写为 `verify_m1` + `mock_gsi`；02-4 `README.md` §六 POC 行改为指向本文 | 本文件进入 02-3 文件清单 | 两份 README 不再指向已删文件 |

> **关于 `CONTRACT_V1_ENABLE=false` 的语义变更**：现在它的作用是「回退到 POC 对照排查」，阶段 0 之后不再有 POC，只能表达「下线 /v1」。若确需对照 POC 行为，用 tag 单独起分支，不要在生产主干保留第二套 `/v1` 实现。
>
> **共享层不随 POC 下线（2026-10-30 重构）**：阶段 0 的“删实现代码”只删 `app/poc/` 里的**适配层与业务骨架**。`app/core/security_signature.py`（验签/防重放/授权时效算法）与 `setting.py` 的 `CONTRACT_*` 签名参数组是 POC 与 contract 共用的生产代码，必须保留；因此 0.3 的验收是“`app.poc` 引用清零”，**不是**“签名实现清零”。好处：POC 下线与签名口径无关，不会再出现“删一套实现时把另一套的算法也带走”的风险。

---

## 三、阶段 1–5：5 条接口按生产口径落进 contract

> 共同前置（P1 已交付，无需再做）：四头签名与 nonce 台账、三类幂等依赖（`idempotent_job/request/event`）、契约错误码与错误体、`ContractApiRoute` 审计留痕。
> 因此每条接口只需实现「业务 + 回执」，在骨架函数体内替换 `_skeleton()`，**签名/审计/幂等依赖不动**。

### 3.1 #01 `POST /parse/jobs` → T2.1（1.5 人日）

| 项 | 内容 |
| --- | --- |
| 数据落点 | `sbp_ai_kb_task`（+ `sbp_ai_kb_document` 关联）；不新建表 |
| 可复用 | `integration_kb.pipeline.parsers.parse_document`、`pipeline.chunker.split_text`、`runtime.executor.run_in_parse_pool`、S3 客户端；POC `parser.fetch_file` 的流式拉取 + 超限中断逻辑可照抄（tag 参照） |
| 必须新增 | `file_url` 拉取与 10MB/类型校验（413 `PAYLOAD_TOO_LARGE`）、`job_id` 幂等回执（依赖已提供 `reservation`）、202 受理后后台执行 |
| 验收断言 | 202 + `job_id` 落 `sbp_ai_kb_task`；同 `job_id` 同正文回放 202（不新增任务）；同 `job_id` 异正文 409；超限 413；缺签名头 401 |
| 依赖疑点 | Q8（失败只能经 #02 暴露）、Q10（`metadata` 白名单） |

### 3.2 #02 `GET /parse/jobs/{job_id}` → T2.2 + T2.3（2.0 + 2.5 人日）

| 项 | 内容 |
| --- | --- |
| 数据落点 | `sbp_ai_kb_task`（状态机）+ `sbp_ai_kb_chunk`（结果投影） |
| 可复用 | `integration_kb/task` 的查询与状态字段；`integration_kb/pipeline/document_pipeline.py` 的产物结构 |
| 必须新增 | `queued/running/succeeded/failed` + `progress`；`failed` 走 **200 + `error_code`**；`chunks(seq/heading/page_from/page_to/source_type)` 投影；`suggested` 与 `warnings` |
| 验收断言 | 四态推进可观测；`failed` 返回 200 且带 `error_code`；未知 `job_id` 404 `NOT_FOUND`；`pages` 无能力时 `[]`（Q12） |
| 依赖疑点 | Q8、Q12 |

### 3.3 #03 `POST /knowledge/entries` → T3.1 + T3.2 + T3.3（2.5 + 3.0 + 2.0 人日）

| 项 | 内容 |
| --- | --- |
| 数据落点 | `publication` / `revision` / `chunk` 三张 `sbp_ai_kb_*` 表（表名以 T3.1 定稿为准） |
| 可复用 | **POC 之外没有等价物**：管理台是「自己解析文件」的路径，从不接收 GSI 传来的切片；POC `publish.write_publication` 是唯一参考（切片集合校验 → bge-m3 批量向量化 → pgvector upsert） |
| 必须新增 | 首次发布不可隐式覆盖（`publication_id` 已存在 → 409，码值待 Q7）；`content_hash` 校验（Q9）；批量 embedding（`EMBEDDING_BATCH_SIZE`）+ 大集合限流；修订/删除四路径（#05/#06 同批）；删除留墓碑 `expires_at=NULL` |
| 验收断言 | 首发 201；同 `request_id` 同正文回放 201；同 `publication_id` 不同 `request_id` → 409；`content_hash` 不符 → 409；向量条数 = 切片条数 |
| 依赖疑点 | Q7、Q9、Q10、Q14；**这是 5 条里增量最大的接口** |

### 3.4 #07 `POST /knowledge/search` → T4.1 + T4.2 + T4.3（1.0 + 2.5 + 2.5 人日）

| 项 | 内容 |
| --- | --- |
| 数据落点 | `sbp_ai_kb_chunk`（向量）+ 8 张 GSI 共享表（只读） |
| 可复用 | `integration_kb.retrieval.search_knowledge`（关键词 + 向量）、`integration_kb/clients/{embedding,rerank}`；POC `retrieval.py` 的 RRF 融合与结果投影（`public_hit` 不回 `content`） |
| 必须新增 | rerank 精排；**检索期权限下推**（`CONTEXT_OUT_OF_SCOPE` 403 与越权 0 命中）；`score` 归一口径（Q5：仅同轮排序可比） |
| 验收断言 | `returned_count == len(hits)`、`no_evidence` 与 `hits` 空否一致；越权切片 0 命中；共享表不可用 → 503 `AUTHORIZATION_UNAVAILABLE`（**禁止降级全库检索**） |
| 依赖疑点 | Q5；**T4.3 是本期最高风险（依赖 GSI 8 张表授权，W10）** |

### 3.5 #14 `POST /chat/completions` → T5.1 + T5.3 + T5.4 + T5.5（1.0 + 1.5 + 2.0 + 2.5 人日）

| 项 | 内容 |
| --- | --- |
| 数据落点 | 会话/消息表（T5.1）+ `sbp_ai_system_request_log`（超时承接） |
| 可复用 | `integration_chat.pipeline.run_chat_pipeline`、`integration_chat.cite.build_cites/build_followups/sanitize_citations`；POC `chat.py` 的 citations 逐字引文 / `source_snapshots` 结构可作字段参照 |
| 必须新增 | **非流式分支**（生产侧只有 SSE：`service.stream_chat` 返回 `AsyncGenerator`，契约 `stream=false` 要一次性 JSON）；历史回灌（`session_id` 维度）；引用编号后校验 + 重试一次；`contexts` 校验与越权 403 |
| 验收断言 | 非流式 JSON 含 `answer/citations/source_snapshots/usage`；`citations[].quote` 是切片正文逐字子串；多轮第二轮能带历史；模型超时 → 500 `INTERNAL_ERROR` + `retryable=true`（Q13） |
| 依赖疑点 | Q3（`citation_id` 来源）、Q4（首轮 `session_id`）、Q13（模型超时归属） |

### 3.6 #15 `POST /chat/requests/{target_request_id}/query` → T5.5 同批

| 项 | 内容 |
| --- | --- |
| 数据落点 | `sbp_ai_system_request_log`（`operation∈{completion,mutation,delete}` / `status` / `result_visible` / `expires_at`） |
| 可复用 | 表与模型已由 T1.0 建好；P1 只做了骨架 |
| 必须新增 | 结果落库（`#14`/写操作/删除受理时写入）、过期回 409 `RESULT_EXPIRED`（不是 404）、`result_visible=false` 时按契约投影 |
| 验收断言 | 受理即可查（`running`）；完成后返回结果；超 `expires_at` → 409 `RESULT_EXPIRED`；陌生 ID → 404 |

### 3.7 工作量汇总（沿用 `02-03` 计划口径）

与这 5 条接口直接相关的任务合计约 **27.5 人日**（含共享基建 T3.1 版本表、T4.3 权限下推、T5.1 会话模型）；其余 15 条接口（#04–#06、#08–#13、#16–#20）另计。

| 阶段 | 任务 | 人日 |
| --- | --- | --- |
| P2 | T2.1 + T2.2 + T2.3 + T2.4 | 7.0 |
| P3 | T3.1 + T3.2 + T3.3 | 7.5 |
| P4 | T4.1 + T4.2 + T4.3 | 6.0 |
| P5 | T5.1 + T5.3 + T5.4 + T5.5 | 7.0 |

---

## 四、顺序与依赖

```
阶段 0（下线收缩，0.25 人日）
   └─ 立刻可做，不阻塞任何业务任务
阶段 1  P2：T2.1 → T2.2 → T2.3 → T2.4          （#01/#02 打通解析链路）
阶段 2  P3：T3.1 → T3.2 → T3.3                 （#03/#05/#06 写入与版本）
阶段 3  P4：T4.1 → T4.2 → T4.3                 （#07；T4.3 关键路径，需 GSI 授权 W10）
阶段 4  P5：T5.1 → T5.3 → T5.4 → T5.5          （#14/#15）
```

硬依赖：
1. `#01/#02` 先于 `#03`（无解析产物就没有切片可比对，但 `#03` 的入参是 GSI 传的切片，开发可并行，联调顺序不能反）；
2. `T3.1` 版本表先于 `T3.2/T3.3`；
3. `T4.3` 的共享表授权不迟于 W10，否则 `#07` 只能返回 503 而非降级；
4. `#14` 的模型端点（W11）未到位时用 POC 期同一套 OpenAI 兼容 mock 驱动开发。

---

## 五、风险与开放项

| # | 风险 | 影响 | 处置 |
| --- | --- | --- | --- |
| 1 | 删掉 POC 后无回退实现 | 阶段 0 之后 `/v1` 只有 contract 的 501 | 冻结 tag `poc-m0-frozen`，需要对照时切分支；联调前不依赖"回退到 POC" |
| 2 | `CONTRACT_V1_ENABLE=false` 语义变化 | 运维手册里的"回退排查"失效 | 阶段 0 同步改 setting 描述与本文，02-4 §六 更新 |
| 3 | `#03` 生产侧无等价物 | 5 条里增量最大，易低估 | 优先排 T3.1（版本表），T3.2/T3.3 按 3.0+2.0 人日足量排期 |
| 4 | `#14` 生产侧只有 SSE | 直接复用会写出流式实现，偏离契约 | T5.4 明确"非流式一次性 JSON"，`run_chat_pipeline` 抽 `collect` 分支 |
| 5 | `T4.3` 权限未到位 | `#07` 若降级全库检索 = 越权 | 坚持 503 `AUTHORIZATION_UNAVAILABLE`，不做"临时放行"开关 |
| 6 | `poc_v1_*` 删除不可逆 | dev 数据丢失（仅冒烟数据） | 先 `CREATE TABLE ... AS SELECT` 备份或确认后直删 |
| 7 | `mock_gsi` 与 `verify_m1` 职责重叠 | 两套冒烟脚本易分叉 | `verify_m1` 保「M1 门禁 + `--record` 证据」；`mock_gsi` 保「GSI 侧样例 + 负向用例 + 联调」 |

---

## 六、验收口径

**阶段 0 验收**（可脚本化）：

```bat
cd /d d:\workspace\ws_work\fastapi_admin_xian\service_brain_agent\backend
.venv\Scripts\python.exe -m pytest -q                       rem 全绿；条数按本文 0.5 估算
.venv\Scripts\python.exe -m ruff check app tests             rem All checks passed
.venv\Scripts\python.exe -c "from app import create_app; print(len(create_app().openapi()['paths']))"
                                                             rem /v1 仍是 20 条，路径数不减
```

- 启动日志无 `POC /v1`；`information_schema.tables like 'poc_v1_%'` 为空；
- `grep -rn "app.poc" backend/` 无结果（文档中的历史引用除外）。

**每条接口落地验收**（阶段 1–5 通用）：

1. `pytest` 增加对应断言（正常 + 幂等回放 + 冲突 + 越权/过期）；
2. 用 `mock_gsi` 打一遍该接口，`request_id` 能在**系统管理 → 接入调用追踪**（`/system/trace`）里把 `api_log / nonce / idempotency / request_log` 串成一条链路——四表齐了才算"接口真落地"；
3. `verify_m1 --record` 重跑仍 38/38（骨架项随实现替换为 2xx/4xx，验收表口径同步更新）。
