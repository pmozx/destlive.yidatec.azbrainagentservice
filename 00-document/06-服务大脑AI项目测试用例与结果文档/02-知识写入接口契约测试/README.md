# 02 · 知识写入接口契约测试

> 测试范围：`/v1/knowledge/entries` 系列（03.1 新增 / 03.2 读取 / 03.3 修改 / 03.4 删除）
> 测试日期：2026-11-03
> 测试结果：**14/14 全部通过（100%）**

---

## 一、文档清单

| 文档 | 内容 | 更新日期 |
| --- | --- | --- |
| [01-知识写入接口出入参测试报告.md](01-知识写入接口出入参测试报告.md) | 完整测试报告：14 个用例的实际出入参、异常场景、契约符合性 | 2026-11-03 |

---

## 二、测试覆盖

### 正向流程（CRUD 全链路）

| 步骤 | 接口 | 方法 | 路径 | 验证点 |
|------|------|------|------|--------|
| 1 | 03.1 新增 | POST | `/v1/knowledge/entries` | 201 Created，写入回执 |
| 2 | 03.2 读取 | POST | `/v1/knowledge/entries/{id}/query` | 200 OK，完整基线快照 |
| 3 | 03.3 修改 | PUT | `/v1/knowledge/entries/{id}` | 200 OK，revision 递增 |
| 4 | 03.2 读取 | POST | `/v1/knowledge/entries/{id}/query` | 200 OK，修订后内容正确 |
| 5 | 03.4 删除 | DELETE | `/v1/knowledge/entries/{id}` | 200 OK，deleted=true |
| 6 | 03.2 读取 | POST | `/v1/knowledge/entries/{id}/query` | 404 NOT_FOUND，已删除不可读 |

### 异常场景

| 序号 | 场景 | 期望 HTTP | 期望错误码 | 结论 |
|------|------|-----------|------------|------|
| 7 | 重复创建同 publication_id | 409 | STALE_REVISION | ✅ |
| 8 | content_hash 不一致 | 409 | STALE_REVISION | ✅ |
| 9 | base_record_id 不匹配 | 409 | STALE_REVISION | ✅ |
| 10 | 查询不存在的 publication | 404 | NOT_FOUND | ✅ |
| 11 | 必填字段缺失 | 400 | INVALID_REQUEST | ✅ |
| 12 | chunks.seq 不连续 | 400 | INVALID_REQUEST | ✅ |
| 13 | 删除不存在的 publication | 404 | NOT_FOUND | ✅ |
| 14 | resource_type 非法值 | 400 | INVALID_REQUEST | ✅ |

---

## 三、测试脚本

```bash
cd backend
.venv\Scripts\python.exe tests\contract\run_knowledge_tests.py
```

测试结果存档：`backend/tests/contract/test_results_knowledge.json`

---

## 四、与其他文档的关系

| 文档 | 关系 |
| --- | --- |
| [../../01-服务大脑AI项目需求与架构文档/05-接口契约与数据基线/01-知识平台与AI接口对接说明-v3.md](../../01-服务大脑AI项目需求与架构文档/05-接口契约与数据基线/01-知识平台与AI接口对接说明-v3.md) | 接口契约手册 §六.03.1–03.4 |
| [backend/app/modules_api/contract/knowledge/controller.py](../../../backend/app/modules_api/contract/knowledge/controller.py) | 路由实现 |
| [backend/app/modules_api/contract/knowledge/schema.py](../../../backend/app/modules_api/contract/knowledge/schema.py) | Schema 定义 |
| [backend/app/modules_api/contract/knowledge/service.py](../../../backend/app/modules_api/contract/knowledge/service.py) | 业务逻辑实现 |
