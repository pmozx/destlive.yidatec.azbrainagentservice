# 06 · 服务大脑AI项目测试用例与结果文档

> 回答「**如何验证系统行为**」。收录各接口的测试用例、测试数据与测试结果。
> 与 `04`（开发过程文档）的区别：本目录聚焦**测试活动本身**，包含可执行的测试用例与实测结果。
> 文档按编号平铺（不再为每个接口建子目录），数据统一放 `test-data/`。
> 归集起始 2026-11-03

---

## 一、文档清单

> 用例设计与实测记录合并在同一份测试报告中（不再单独维护「测试用例」文档），一份报告 = 一个被测接口族的契约说明 + 用例设计 + 实测结果。

| 编号 | 文档 | 测试范围 | 内容 |
| --- | --- | --- | --- |
| 01 | [01-解析接口出入参测试报告.md](01-解析接口出入参测试报告.md) | `/v1/parse/jobs`（#01）、`/v1/parse/jobs/{job_id}`（#02） | 接口契约与字段约束、9 个用例的设计与实测、轮询与状态流转、结论与执行方式 |
| 02 | [02-知识写入接口出入参测试报告.md](02-知识写入接口出入参测试报告.md) | `/v1/knowledge/entries`（03.1–03.4） | 知识 CRUD 全链路的用例设计与实测记录 + 8 个异常场景 |
| 03 | [03-检索接口出入参测试报告.md](03-检索接口出入参测试报告.md) | `/v1/knowledge/search`（#07） | 检索契约与字段约束、授权先于召回的 7 个正向用例 + 7 个异常场景（含 `natural_language` 501 待交付记录） |
| — | [test-data/](test-data/README.md) | 全部契约测试 | 请求数据（`requests/`）、样例文档（`source_file/`）与实测结果（`responses/`）；测试代码包内不存任何 JSON |

---

## 二、测试覆盖

### 2.1 解析接口（01）

1. **正向场景**：验证 `#01` 提交文档解析与 FAQ 解析任务的完整出入参契约
2. **反向场景**：验证文件类型不支持、文件超限、FAQ 条件必填缺失等异常处理
3. **幂等性**：验证同 `job_id` 重试不重复创建任务
4. **状态机**：验证 `#02` 查询的四态流转（queued → running → succeeded / failed）
5. 实测 9/9 全部通过；`pages` 为对象存储预签名 URL（13 页）

### 2.2 知识写入（02）

- **正向 CRUD 全链路**：新增（201）→ 读取基线（200）→ 修改（200，revision 递增）→ 读取修订版（200）→ 删除（200，`deleted=true`）→ 读取已删除（404）
- **异常场景**：重复创建、`content_hash` 不一致、`base_record_id` 不匹配（均为 409 STALE_REVISION），publication 不存在、删除不存在（404 NOT_FOUND），必填字段缺失、`chunks.seq` 不连续、`resource_type` 非法（400 INVALID_REQUEST）
- 实测 14/14 全部通过

### 2.3 检索（03）

- **正向场景**：关键词命中（多词 AND、`score` 与 `snippet` 口径）、`limit` 截断、库外词无证据、授权与租户隔离（越权 / 无团队 / 非数字 `user_id` / 异租户均 0 命中）
- **异常场景**：`natural_language` 返回 501（T4.2 未交付）、`authorization` 跨度超限 400、已过期 401、缺块 400、空白 `query` / `limit` 越界 / `mode` 非法均 400
- 实测已交付用例 13/13 通过；`natural_language` 1 项因功能未交付标记未通过，待 T4.2 交付后重跑

---

## 三、测试辅助工具

为简化契约测试流程，项目提供了测试辅助接口（POST 与 GET 共用同一处理函数）：

| 项 | 说明 |
| --- | --- |
| 路径 | `POST` / `GET` `/v1/test/signed-headers` |
| query 参数 | `method`（必填，GET/POST/PUT/DELETE）、`path`（必填）、`query`（可选，不含 `?` 前缀） |
| 请求体 | **完整的原始请求体**，服务端逐字节算摘要；GET 传空正文 |
| 服务端自决 | `client_id` 取 `CONTRACT_V1_CLIENT_ID`、`timestamp` 取当前毫秒、`nonce` 每次随机，调用方无需传 |
| 返回 | `headers`、`signing_string`（7 行签名原文）、`client_id`、`timestamp_ms`、`nonce` |
| 后台日志 | 输出请求体、请求体长度、请求体 SHA-256、自定义安全头、Apifox 请求头（与 `tests/contract/debug_signed_headers.py` 同一份调试信息） |

**启用条件**：`env/.env.dev` 中配置 `CONTRACT_TEST_ENABLE=true`，且 `CONTRACT_V1_SHARED_SECRET` 有值

**安全提示**：测试辅助接口**不经过签名验证**，生产环境必须关闭。

---

## 四、测试执行

```bash
cd backend

# 1. 生成签名头（读 01_parse_jobs_doc.json，输出可直接粘贴到 Apifox / curl）
.venv\Scripts\python.exe -m tests.contract.debug_signed_headers

# 2. 解析接口契约测试（01），回写 test-data/responses/（一请求一响应，与 requests/ 同名）
.venv\Scripts\python.exe -m tests.contract.run_parse_tests

# 3. 知识写入接口契约测试（02），同样按同名规则回写 test-data/responses/（16 个请求 → 16 个响应）
.venv\Scripts\python.exe -m tests.contract.run_knowledge_tests

# 4. 检索接口契约测试（03）：先铺场再检索，按同名规则回写 test-data/responses/（15 个请求 → 15 个响应）
.venv\Scripts\python.exe -m tests.contract.run_search_tests
```

需先启动服务（默认 `127.0.0.1:8002`）。改动 `test-data/requests/` 后要重跑对应脚本，否则 `responses/` 里的请求快照与实际入参不符。

---

## 五、与其他目录的关系

| 目录 / 文件 | 关系 |
| --- | --- |
| [`../01-服务大脑AI项目需求与架构文档/`](../01-服务大脑AI项目需求与架构文档/README.md) | 接口契约手册定义了接口的规范，本目录验证实际实现是否符合规范 |
| [契约手册 §六 · 01–03.4](../01-服务大脑AI项目需求与架构文档/05-接口契约与数据基线/01-知识平台与AI接口对接说明-v3.md) | 用例字段与手册逐条对照 |
| [契约手册 §七 · 04 关键词 / 自然语言检索](../01-服务大脑AI项目需求与架构文档/05-接口契约与数据基线/01-知识平台与AI接口对接说明-v3.md) | #07 检索用例字段与手册逐条对照 |
| [`../../backend/app/modules_api/kb/`](../../backend/app/modules_api/kb/) | 被测实现：`parse/`（#01/#02）、`knowledge/`（03.1–03.4 与 #07）与 `kb/retrieval/`（检索通道）；协议基座见 `backend/app/modules_api/base/` |
| [`../04-服务大脑AI项目开发过程文档/`](../04-服务大脑AI项目开发过程文档/README.md) | POC 验证与里程碑验收的概要记录，本目录提供详细的测试用例与实测结果 |
