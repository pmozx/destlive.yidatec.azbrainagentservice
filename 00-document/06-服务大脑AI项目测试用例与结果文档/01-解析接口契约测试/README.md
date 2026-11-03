# 01 · 解析接口契约测试

> 测试范围：`/v1/parse/jobs`（#01）与 `/v1/parse/jobs/{job_id}`（#02）的完整出入参契约验证
> 起始日期：2026-11-03
> 用例来源：`backend/tests/contract/debug_json/01_parse_jobs_doc.json` 与 `01_parse_jobs_fqa.json`

---

## 一、文档清单

| 文档 | 内容 | 更新日期 |
| --- | --- | --- |
| [01-提交解析任务接口测试用例.md](01-提交解析任务接口测试用例.md) | #01 接口完整用例：文档解析 / FAQ 解析、异常场景、字段校验清单 | 2026-11-03 |
| [02-查询解析进度接口测试用例.md](02-查询解析进度接口测试用例.md) | #02 接口详细测试用例：状态流转、轮询策略、响应字段说明 | 2026-11-03 |
| [01-解析接口出入参测试报告.md](01-解析接口出入参测试报告.md) | **测试结果汇总**：9 个用例的实际执行情况、出入参记录、问题分析 | 2026-11-03 |

---

## 二、测试目标

1. **正向场景**：验证 `#01` 提交文档解析与 FAQ 解析任务的完整出入参契约
2. **反向场景**：验证文件类型不支持、文件超限、FAQ 条件必填缺失等异常处理
3. **幂等性**：验证同 `job_id` 重试不重复创建任务
4. **状态机**：验证 `#02` 查询的四态流转（queued → running → succeeded / failed）

---

## 三、测试辅助接口

为简化契约测试流程，项目提供了测试辅助接口（需开启 `CONTRACT_TEST_ENABLE=true`）：

| 接口 | 方法 | 路径 | 用途 |
|------|------|------|------|
| 签名头生成 | POST | `/v1/test/signed-headers` | 生成合法的 HMAC 签名头 |
| 签名头生成（简化） | GET | `/v1/test/signed-headers` | GET 请求的简化版签名头生成 |

**使用方法**：详见 [01-提交解析任务接口测试用例.md](01-提交解析任务接口测试用例.md) §六

**安全提示**：测试辅助接口**不经过签名验证**，生产环境必须关闭（`CONTRACT_TEST_ENABLE=false`）。

---

## 四、与其他文档的关系

| 文档 | 关系 |
| --- | --- |
| [../../01-服务大脑AI项目需求与架构文档/05-接口契约与数据基线/01-知识平台与AI接口对接说明-v3.md](../../01-服务大脑AI项目需求与架构文档/05-接口契约与数据基线/01-知识平台与AI接口对接说明-v3.md) | 接口契约手册 §七 · 01 / 02，本文档用例与其字段表逐条对照 |
| [backend/tests/contract/debug_json/01_parse_jobs_doc.json](../../../backend/tests/contract/debug_json/01_parse_jobs_doc.json) | 用例 #1 入参 JSON 来源（文档解析） |
| [backend/tests/contract/debug_json/01_parse_jobs_fqa.json](../../../backend/tests/contract/debug_json/01_parse_jobs_fqa.json) | 用例 #2 入参 JSON 来源（FAQ 解析） |
