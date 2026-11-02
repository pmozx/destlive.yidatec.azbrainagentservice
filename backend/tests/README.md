# 后端测试目录约定

测试按**被测对象**分包，不再按"哪个批次发现的"命名。曾经的 `test_entry_fields.py`（P0-10 批次）、
`test_soft_delete_removed.py`（P0-4 批次）、`test_v1_contract.py`（M1 里程碑）这类名字，
除了写它的人，没人知道测的是什么——这就是重组的原因。

## 一、目录结构与判断规则

```
tests/
├── conftest.py               # 模块级连接池隔离（全包共用）
├── support.py                # 共享装置：常量、签名头、进程内调用、requires_db
├── core/                     # 基座能力：与业务语义无关
├── contract/                 # 契约接入层（app/contract + 对外 API 行为）
├── modules/
│   ├── kb/                   # 业务域：知识库
│   └── system/               # 业务域：系统管理
└── stages/                   # 阶段验收：一个子包 = 一个里程碑
    └── m1_contract/          #   M1 契约接入层骨架
```

**放哪个包，只问一个问题：这段断言在保护谁？**

| 包 | 判据 | 典型内容 |
|---|---|---|
| `core/` | 换成任何业务域都成立 | 基座模型与索引口径、CRUD 条件构造、事务出口、请求体缓存、向量纯函数、配置校验 |
| `contract/` | 只对"按契约接入的调用方"有意义 | 错误码表、签名算法与拒绝原因、幂等台账、审计留痕、路由鉴权行为 |
| `modules/<域>/` | 只对某个业务域成立 | 发布单元 CAS、归属校验、解析任务状态机、写入链路 |
| `stages/<里程碑>/` | **只对某个阶段成立，会过期** | 路由清单锁定、骨架 501 占位、阶段性的字段迁移验收 |

## 二、命名规则

- **包名 = 被测对象所在层**：`modules/kb/` 对应 `app/modules/kb/`，一一对应，不发明新层级；
- **文件名 = 被测对象**，不是缺陷编号、不是批次号、不是里程碑：
  `test_publication.py`（不是 `test_publication_cas.py` + `test_entry_fields.py` 两个），
  `test_signature.py`（不是 `test_security_signature.py` + 契约文件里的签名节）；
- **一个对象一个文件**：同一对象的 CAS、校验、字段落点放一起，读者要一次看全；
- **类/函数名说清断言什么**：`test_scope_rejects_cross_tenant_write` 优于 `test_scope_2`。

## 三、`stages/` 的生命周期（重要）

阶段包里的断言**会过期，且过期后必须删而不是改**，所以隔离出来，避免和长期回归混在一起：

1. 一个子包 = 一个里程碑，命名 `m1_*/`、`m2_*/`；
2. 每个文件顶部写明"这一阶段在验什么、什么时候该删"；
3. 阶段收尾时**整包复核**：实现落地后，占位断言应删除（而不是改成断言成功）；
4. 判定标准：如果这条断言在"下一阶段"仍然必须为真，它就不属于 `stages/`，应移到
   `core/`、`contract/` 或 `modules/`。

## 四、共享装置：`support.py`

跨包复用的东西只放这里，避免每个包各写一份后逐渐漂移：

- 常量：`SECRET` / `CLIENT_ID` / `NOW_MS` / `PATH` / `BODY`（签名口径统一）
- `requires_db`：真实 PG 不可达时自动 skip（台账类用例需要 JSONB 与 `ON CONFLICT`）
- `make_request()`：不经过 HTTP 构造 `Request`（审计组装用例）
- `call()`：`ASGITransport` 进程内调用应用（路由/解析用例）

**约定**：只放与具体域无关的通用装置。解析任务正文、知识条目正文这类构造留在各包内。

## 五、调试脚本不是测试

`contract/debug_signed_headers.py` 是**手工调试工具**（生成可直接粘贴的 curl / Apifox 签名头），
故意不叫 `test_*`，pytest 不会收集它：

```bash
cd backend && uv run python -m tests.contract.debug_signed_headers
```

它原来叫 `test_generate_custom_headers`，留在测试集里有两个问题：只有 print 没有断言；
唯一的 assert（密钥非空）在 test/uat/prod 会误报——那三个环境的密钥是**故意留空待 GSI 下发**的。
**约定**：只打印不断言、或断言依赖具体环境的东西，一律做成脚本放同包下，不占测试用例。

## 六、旧文件名对照

重组前的文件与本目录的对应关系（历史文档里仍可能引用旧名）：

| 重组前 | 现在 |
|---|---|
| `test_base_model_defaults.py` + `test_soft_delete_removed.py` | `core/test_base_model.py` |
| `test_base_crud_conditions.py` | `core/test_base_crud.py` + `core/test_settings.py` |
| `test_db_getter_transaction.py` | `core/test_db_getter.py` |
| `test_vector_store_sql.py` | `core/test_vector_store.py` |
| `test_security_signature.py` + `test_v1_contract.py` 的签名节 | `contract/test_signature.py` |
| `test_idempotent_guard_release.py` + `test_v1_contract.py` 的幂等/nonce 节 | `contract/test_idempotency.py` |
| `test_v1_contract.py` 的错误码 / 审计 / 路由节 | `contract/test_error_codes.py` / `test_audit.py` / `test_routes.py` |
| `test_v1_contract.py` 的骨架与路由清单节 | `stages/m1_contract/test_skeleton.py` |
| `test_v1_contract.py` 的解析链路节 | `modules/kb/test_parse_job.py` |
| `test_publication_cas.py` + `test_entry_fields.py` | `modules/kb/test_publication.py` |
| `test_v1_knowledge_entries.py` | `modules/kb/test_entry_write.py` |
| `test_access_trace.py` | `modules/system/test_access_trace.py` |

## 七、当前基线

```bash
cd backend && uv run pytest -q     # 132 passed
```

| 包 | 用例数 | 文件 |
|---|---|---|
| `core/` | 51 | base_model 9 / base_crud 2 / settings 3 / db_getter 3 / request_body 5 / chat_cite 4 / vector_store 20 / embedding_client 5 |
| `contract/` | 44 | signature 23 / idempotency 10 / error_codes 3 / audit 3 / routes 5 |
| `modules/` | 34 | kb 25（parse_job 4 / publication 10 / entry_write 11）；system 9（access_trace） |
| `stages/` | 3 | m1_contract：路由清单 / 错误响应声明 / 501 骨架 |
